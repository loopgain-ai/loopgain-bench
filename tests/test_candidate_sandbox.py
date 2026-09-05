"""Synthetic sandbox checks; integration requires an existing local image."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from bench.workloads._shared import sandbox

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = pytest.mark.skipif(
    os.environ.get("BENCH_SANDBOX_INTEGRATION") != "1",
    reason="opt in with BENCH_SANDBOX_INTEGRATION=1; never pulls images",
)


def test_container_has_mandatory_boundaries(tmp_path):
    cmd = sandbox._command("docker", "sha256:" + "a" * 64, "test", tmp_path)
    for flag in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65534:65534",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=32",
        "--memory=128m",
        "--memory-swap=128m",
        "--cpus=1",
        "--log-driver=none",
    ):
        assert flag in cmd
    assert cmd.count("--mount") == 1
    assert f"type=bind,src={tmp_path},dst=/input,readonly" in cmd
    assert "-i" in cmd
    assert not any("docker.sock" in x for x in cmd)


def test_missing_docker_fails_closed(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: None)
    with pytest.raises(sandbox.SandboxUnavailable, match="no host fallback"):
        sandbox.ensure_available()


def test_missing_image_does_not_pull(monkeypatch):
    calls = []

    def unavailable(cmd, **kwargs):
        calls.append(cmd)
        raise sandbox.subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(sandbox.subprocess, "run", unavailable)
    with pytest.raises(sandbox.SandboxUnavailable, match="no automatic pull"):
        sandbox._local_image("docker")
    assert len(calls) == 1
    assert calls[0][1:3] == ["image", "inspect"]


def test_preflight_prevents_model_request(monkeypatch):
    from bench.workload import TrialInput
    from bench.workloads._shared import codegen_base

    invoke = Mock(side_effect=AssertionError("model must not be called"))
    monkeypatch.setattr(codegen_base, "invoke", invoke)
    monkeypatch.setattr(
        codegen_base,
        "ensure_available",
        Mock(side_effect=sandbox.SandboxUnavailable("offline")),
    )
    with pytest.raises(sandbox.SandboxUnavailable):
        codegen_base.CodegenWorkload().run_iteration(
            TrialInput(0, "synthetic", {}, {}), None, 1, None
        )
    invoke.assert_not_called()


@INTEGRATION
def test_benign_candidate_pass_fail():
    worker = ROOT / "bench/workloads/_shared/_codegen_exec_worker.py"
    result = sandbox.execute_worker(
        worker,
        {
            "code": "def add(a,b): return a+b",
            "entry_point": "add",
            "tests": ["add(1,2)==3", "add(1,2)==4"],
        },
        10,
    )
    assert result["n_passing"] == 1
    assert result["n_total"] == 2


@INTEGRATION
def test_effective_container_boundaries(monkeypatch):
    monkeypatch.setenv("SYNTHETIC_HOST_SECRET", "must-not-reach-candidate")
    worker = """import os,json,pathlib
try:
 pathlib.Path('/input/request.json').write_text('changed')
 writable=True
except OSError:
 writable=False
pathlib.Path('/tmp/scratch').write_text('ok')
print(json.dumps(dict(uid=os.getuid(), env=dict(os.environ), writable=writable,
 memory=pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip(),
 pids=pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip(),
 routes=pathlib.Path('/proc/net/route').read_text().splitlines(),
 status=pathlib.Path('/proc/self/status').read_text())))"""
    data = json.loads(sandbox._run(sandbox.ensure_available(), worker, {}, 10))
    assert data["uid"] == 65534
    assert "SYNTHETIC_HOST_SECRET" not in data["env"]
    assert set(data["env"]) <= {"PATH", "HOME", "LC_CTYPE"}
    assert not data["writable"]
    assert data["memory"] == "134217728"
    assert data["pids"] == "32"
    assert len(data["routes"]) == 1  # no routes beyond the table header
    assert "CapEff:\t0000000000000000" in data["status"]
    assert "NoNewPrivs:\t1" in data["status"]


@INTEGRATION
@pytest.mark.parametrize(
    "worker,timeout,match",
    [
        ("import time; time.sleep(30)", 0.5, "timeout"),
        ("print('x'*70000)", 10, "output limit"),
    ],
)
def test_limits_remove_container(monkeypatch, worker, timeout, match):
    from types import SimpleNamespace

    name = "loopgain-candidate-synthetic-limit-test"
    monkeypatch.setattr(
        sandbox.uuid, "uuid4", lambda: SimpleNamespace(hex="synthetic-limit-test")
    )
    image = sandbox._local_image(sandbox._docker())
    with pytest.raises(sandbox.SandboxExecutionError, match=match):
        sandbox._run(image, worker, {}, timeout)
    result = sandbox.subprocess.run(
        [sandbox._docker(), "container", "inspect", name],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert b"No such" in result.stderr


@INTEGRATION
def test_wrong_fixed_point_benign_candidate():
    from analysis.wrong_fixed_point_w1 import run_worker

    result = run_worker(
        "def add(a,b): return a+b", "add", [("[1,2]", "3"), ("[1,2]", "4")]
    )
    assert result["n_total"] == 2
    assert result["strict_fail"] == 1
    assert result["norm_fail"] == 1


def test_cleanup_failure_stops_run(monkeypatch):
    import subprocess

    monkeypatch.setattr(sandbox, "_docker", lambda: "docker")

    def failed_create_and_cleanup(cmd, **kwargs):
        if cmd[1] == "create":
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 1, b"", b"daemon unavailable")

    monkeypatch.setattr(sandbox.subprocess, "run", failed_create_and_cleanup)
    with pytest.raises(sandbox.SandboxUnavailable, match="cleanup"):
        sandbox._run("sha256:" + "a" * 64, "print('unused')", {}, 1)


@INTEGRATION
def test_worker_exit_removes_background_child(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        sandbox.uuid, "uuid4", lambda: SimpleNamespace(hex="synthetic-child-test")
    )
    image = sandbox._local_image(sandbox._docker())
    result = sandbox._run(
        image,
        "import subprocess; subprocess.Popen(['sleep','30']); print('done')",
        {},
        10,
    )
    assert result.strip() == "done"
    check = sandbox.subprocess.run(
        [
            sandbox._docker(),
            "container",
            "inspect",
            "loopgain-candidate-synthetic-child-test",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert check.returncode != 0
    assert b"No such" in check.stderr
