"""Runner cancellation only: synthetic workloads, no models or benchmark data."""

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from bench.cancellation import RunAbort, run_scope
from bench.workloads._shared.sandbox import SandboxUnavailable


@pytest.fixture
def runner(monkeypatch):
    # LoopGain's decision algorithm is outside these failure-before-observe tests.
    # Load the actual runner while substituting only the unavailable dependency.
    dependency = ModuleType("loopgain")
    dependency.LoopGain = Mock(
        return_value=SimpleNamespace(
            should_continue=lambda: True,
            result=SimpleNamespace(iterations_used=0),
            observe=Mock(side_effect=AssertionError("must not observe fatal failures")),
        )
    )
    monkeypatch.setitem(sys.modules, "loopgain", dependency)
    spec = importlib.util.spec_from_file_location(
        "bench._abort_test_runner", Path(__file__).parents[1] / "bench/runner.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["baseline", "loopgain"])
def test_cleanup_failure_never_runs_another_iteration(runner, mode):
    workload = SimpleNamespace(
        target_error=0,
        run_iteration=Mock(side_effect=SandboxUnavailable("cleanup unconfirmed")),
    )
    abort = RunAbort()
    with pytest.raises(SandboxUnavailable, match="cleanup unconfirmed"):
        if mode == "baseline":
            runner._run_baseline(workload, None, 5, None, abort)
        else:
            runner._run_loopgain(workload, None, None, abort)
    assert workload.run_iteration.call_count == 1
    assert abort.stopped.is_set()


def test_ordinary_iteration_error_keeps_existing_semantics(runner):
    outcome = SimpleNamespace(
        output="ok",
        error=0,
        completion=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    workload = SimpleNamespace(
        run_iteration=Mock(side_effect=[ValueError("ordinary failure"), outcome])
    )
    result = runner._run_baseline(workload, None, 2, None)
    assert result["failed_iters"] == [1]
    assert result["final_error"] == 0


def test_serial_cell_propagates_failure_without_next_trial(
    runner, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner, "RAW_DIR", tmp_path)
    trial = Mock(side_effect=SandboxUnavailable("cleanup unconfirmed"))
    monkeypatch.setattr(runner, "run_trial", trial)
    workload = SimpleNamespace(id="synthetic", model="unused", to_metadata=dict)
    with pytest.raises(SandboxUnavailable):
        runner.run_cell(workload, 10)
    assert trial.call_count == 1
    assert len((tmp_path / "synthetic-untagged.jsonl").read_text().splitlines()) == 1


def test_concurrent_failure_stops_siblings_and_further_scheduling(runner):
    abort = RunAbort()
    barrier = threading.Barrier(2)
    started = []

    def job(item):
        with run_scope(abort):
            started.append(item)
            barrier.wait(timeout=2)
            if item == 0:
                raise SandboxUnavailable("cleanup unconfirmed")
            assert abort.stopped.wait(2)
            abort.check()
            raise AssertionError("later work ran")

    with pytest.raises(SandboxUnavailable):
        list(runner._parallel(job, range(20), 2, abort))
    assert sorted(started) == [0, 1]


def test_trial_condition_failure_propagates_without_model_calls(runner, monkeypatch):
    monkeypatch.setattr(runner, "client_for_model", lambda *a, **k: None)
    workload = SimpleNamespace(
        model="unused",
        target_error=0,
        generate_trial=Mock(return_value=None),
        run_iteration=Mock(side_effect=SandboxUnavailable("cleanup unconfirmed")),
    )
    abort = RunAbort()
    with pytest.raises(SandboxUnavailable):
        runner.run_trial(workload, 0, abort)
    # Conditions already in flight may fail too, but none starts a second iteration.
    assert 1 <= workload.run_iteration.call_count <= 4
    assert all(call.args[2] == 1 for call in workload.run_iteration.call_args_list)
    assert abort.stopped.is_set()


def test_abort_during_preflight_prevents_model_call(monkeypatch):
    from bench.workload import TrialInput
    from bench.workloads._shared import codegen_base

    abort = RunAbort()
    monkeypatch.setattr(codegen_base, "ensure_available", abort.stop)
    invoke = Mock(side_effect=AssertionError("model call after abort"))
    monkeypatch.setattr(codegen_base, "invoke", invoke)
    trial = TrialInput(
        0, "synthetic", {"spec": "def add(a,b):", "entry_point": "add", "tests": []}, {}
    )
    with pytest.raises(SandboxUnavailable), run_scope(abort):
        codegen_base.CodegenWorkload().run_iteration(trial, None, 1, None)
    invoke.assert_not_called()


def test_main_stops_before_next_cell(runner, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runner", "--all-cells", "--n", "2"])
    monkeypatch.setattr(runner, "_load_workload", lambda name: SimpleNamespace(id=name))
    run_cell = Mock(side_effect=SandboxUnavailable("cleanup unconfirmed"))
    monkeypatch.setattr(runner, "run_cell", run_cell)
    with pytest.raises(SandboxUnavailable):
        runner.main()
    assert run_cell.call_count == 1
