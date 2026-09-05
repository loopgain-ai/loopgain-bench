"""Cooperative, run-scoped cancellation after a sandbox infrastructure failure."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event

from .workloads._shared.sandbox import SandboxUnavailable


class RunAbort:
    def __init__(self):
        self.stopped = Event()

    def stop(self):
        self.stopped.set()

    def check(self):
        if self.stopped.is_set():
            raise SandboxUnavailable("Run aborted after sandbox infrastructure failure")


_current_abort = ContextVar("bench_run_abort", default=None)


@contextmanager
def run_scope(abort):
    token = _current_abort.set(abort)
    try:
        abort.check()
        yield
    except SandboxUnavailable:
        abort.stop()
        raise
    finally:
        _current_abort.reset(token)


def check_run_active():
    abort = _current_abort.get()
    if abort is not None:
        abort.check()
