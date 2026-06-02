"""Run the bench runner as a fully-detached daemon that survives a parent
session suspend/kill.

macOS has no `setsid` command, but the `os.setsid()` *syscall* exists. We
double-fork + os.setsid() so the worker lands in its own session/process group,
reparented to launchd (PID 1). When the Claude Code session suspends and kills
its process group, this worker is in a different session and survives.

Usage (the foreground call returns immediately; the grandchild runs on):
    .venv/bin/python -m bench_v2.detach_run <logfile> -- <runner args...>

Example:
    .venv/bin/python -m bench_v2.detach_run /tmp/sonnet.log -- \
        --source bird --bird-root ... --provider anthropic --model claude-sonnet-4-6 \
        --n 500 --max-iter 10 --max-spend 40 --out data/results/v2_bird_sonnet_n500.json \
        --i-understand-this-spends-money

NOTE: cwd is preserved (NOT chdir'd to /) so the runner's relative paths work.
The runner's own per-trial checkpointing still applies, so even this can be
resumed if the machine itself reboots.
"""
from __future__ import annotations

import os
import sys


def _daemonize(logfile: str) -> None:
    if os.fork() > 0:
        os._exit(0)                 # original parent returns to the shell immediately
    os.setsid()                     # new session — detach from the caller's session/pgroup
    if os.fork() > 0:
        os._exit(0)                 # second fork — can never reacquire a controlling TTY
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull, "rb", 0) as devnull:
        os.dup2(devnull.fileno(), 0)
    lf = open(logfile, "ab", 0)
    os.dup2(lf.fileno(), 1)
    os.dup2(lf.fileno(), 2)


def main() -> None:
    if "--" not in sys.argv:
        sys.exit("usage: python -m bench_v2.detach_run <logfile> -- <runner args...>")
    sep = sys.argv.index("--")
    logfile = sys.argv[1]
    runner_argv = sys.argv[sep + 1:]
    _daemonize(logfile)
    print(f"[detach_run] pid={os.getpid()} sid={os.getsid(0)} cwd={os.getcwd()}", flush=True)
    from bench_v2.runner import main as runner_main
    runner_main(runner_argv)


if __name__ == "__main__":
    main()
