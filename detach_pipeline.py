"""Run an arbitrary command as a fully-detached daemon that survives a parent
Claude Code session suspend/kill.

WHY THIS EXISTS
---------------
Long unattended bench runs were repeatedly killed mid-run. The cause is NOT
macOS sleep (display-on + AC prevents that, and `caffeinate` was held during
kills) — it is the Claude Code session SUSPENDING during idle gaps, which kills
processes in its process group. macOS has no `setsid` *command*, but the
`os.setsid()` *syscall* exists. We double-fork + os.setsid() so the worker lands
in its own session/process group, reparented to launchd (PID 1), outside the CC
process group, and survives the suspend-kill outright. PROVEN on a Sonnet n=500
run that completed fully unattended across multiple suspends (RUNNING_BENCHMARKS.md §1).

This is the GENERAL-PURPOSE sibling of bench_v2/detach_run.py (which imports
bench_v2.runner directly). This one execs an arbitrary command line, so it can
drive the whole main-bench pipeline: `bench.runner && bench.judge && analysis.run`.

The main bench.runner has per-cell (not per-trial) resume via --skip-existing,
so detachment — not checkpoint resume — is the primary defense here. cwd is
preserved (NOT chdir'd to /) so relative paths in the command still resolve.

USAGE (foreground call returns immediately; the grandchild runs on):
    .venv/bin/python detach_pipeline.py <logfile> -- <command...>

EXAMPLE:
    .venv/bin/python detach_pipeline.py /tmp/run.log -- \
        /bin/zsh -c '.venv/bin/python -m bench.runner --all-cells --n 200 ...'

VERIFY it reparented (PPID should be 1):
    ps -eo pid,ppid,sess,etime,command | grep detach_pipeline | grep -v grep
"""
from __future__ import annotations

import os
import sys


def _daemonize(logfile: str) -> None:
    if os.fork() > 0:
        os._exit(0)                 # original parent returns to the shell immediately
    os.setsid()                     # new session — detach from caller's session/pgroup
    if os.fork() > 0:
        os._exit(0)                 # second fork — can never reacquire a controlling TTY
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "rb", 0) as devnull:
        os.dup2(devnull.fileno(), 0)
    lf = open(logfile, "ab", 0)     # unbuffered append
    os.dup2(lf.fileno(), 1)
    os.dup2(lf.fileno(), 2)


def main() -> None:
    if "--" not in sys.argv or len(sys.argv) < 2:
        sys.exit("usage: python detach_pipeline.py <logfile> -- <command...>")
    sep = sys.argv.index("--")
    logfile = sys.argv[1]
    cmd = sys.argv[sep + 1:]
    if not cmd:
        sys.exit("no command after --")
    _daemonize(logfile)
    print(f"[detach_pipeline] pid={os.getpid()} sid={os.getsid(0)} cwd={os.getcwd()}", flush=True)
    print(f"[detach_pipeline] exec: {' '.join(cmd)}", flush=True)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
