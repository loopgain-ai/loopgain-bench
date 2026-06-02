"""Stage 3 (zero-spend) — LoopGain vs confidence vs naive, simulated on the
existing Stage-0 BIRD trajectories. No API calls.

Each trial has a binary per-iteration error series s (0=correct vs gold, 1=wrong)
and result hashes. We replay four STOP policies on the identical trajectory and
score (a) correctness shipped, (b) iteration cost, (c) false-stops:

  - naive          : run to max_iter, ship terminal output (what produced the FTB rate)
  - confidence(k)  : stop at first s==0 (target) OR after k no-improvement iters; ship best-so-far
  - loopgain       : stop at first s==0 (TARGET_MET) else run to max; ship best-so-far
                     (on a binary oracle, LoopGain's stop == target-met; oscillation detection
                      is degenerate because the only 'good' state is s==0)
  - zero-config    : oracle-free — ship the first output whose result recurs (uses ONLY hashes)

A false-stop = the policy shipped wrong (s=1) on a trial where a correct answer
(s=0) appears at or after the policy's stop index (i.e., it stopped before a
reachable correct).
"""
from __future__ import annotations
import json
from collections import Counter

def load(f): return json.load(open(f))["trials"]

def seqs(t):
    return [p["s"] for p in t["trajectory"]], [p["result_hash"] for p in t["trajectory"]]

def naive(s, h):           # ship terminal
    return len(s) - 1, len(s)
def confidence(s, h, k):   # target short-circuit OR plateau(k); ship best-so-far index
    best = 10**9; bi = 0; cnt = 0
    for i, v in enumerate(s):
        if v == 0:                    # target met (shared with loopgain)
            return i, i + 1
        if v < best: best, bi, cnt = v, i, 0
        else: cnt += 1
        if cnt >= k:
            return bi, i + 1
    return bi, len(s)
def loopgain(s, h):        # target-met stop; else run to max; ship best-so-far
    for i, v in enumerate(s):
        if v == 0:
            return i, i + 1
    bi = s.index(min(s))
    return bi, len(s)
def zeroconfig(s, h):      # oracle-free: first result that later recurs
    for i, x in enumerate(h):
        if x != "ERR" and h[i+1:].count(x) >= 1:
            return i, i + 1
    valid = [x for x in h if x != "ERR"]
    if valid:
        top = Counter(valid).most_common(1)[0][0]; return h.index(top), len(s)
    return len(s)-1, len(s)

def evaluate(trials, policy):
    # population = trials where a correct answer exists somewhere (0 in s), so any
    # wrong ship is a genuine miss/false-stop (it could have shipped correct).
    shipped_correct = iters = 0
    for t in trials:
        s, h = seqs(t)
        idx, used = policy(s, h)
        shipped_correct += (s[idx] == 0)
        iters += used
    n = len(trials)
    missed = n - shipped_correct   # shipped wrong despite a reachable correct
    return shipped_correct, 100*shipped_correct/n, iters/n, missed

def main():
    trials = load("data/results/v2_bird_gpt41mini.json") + load("data/results/v2_bird_sonnet.json")
    reached = [t for t in trials if t["metrics"]["reached_good"]]   # the population where a correct exists
    print(f"All trials: {len(trials)}  | reached-correct (a correct answer exists): {len(reached)}\n")
    print(f"{'policy':22s} {'correct-shipped':>18s} {'mean iters (cost)':>18s} {'missed':>12s}")
    print("-"*74)
    policies = [
        ("naive (run-to-10)", naive),
        ("confidence k=1", lambda s,h: confidence(s,h,1)),
        ("confidence k=2", lambda s,h: confidence(s,h,2)),
        ("confidence k=3", lambda s,h: confidence(s,h,3)),
        ("loopgain (target-met)", loopgain),
        ("zero-config (no oracle)", zeroconfig),
    ]
    for name, fn in policies:
        c, pct, mi, fs = evaluate(reached, fn)
        print(f"{name:22s} {c:3d}/{len(reached)} = {pct:5.1f}%   {mi:6.2f}            {fs:4d}")
    print("\n(reached-correct population; 'correct-shipped' = ships an s==0 output;")
    print(" cost = mean observe() iterations; false-stops = shipped wrong with a correct reachable.)")

if __name__ == "__main__":
    main()
