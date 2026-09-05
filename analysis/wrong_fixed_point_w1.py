"""Wrong-fixed-point measurement on W1 (MBPP+) — correctness-gate Feature-Gate proof.

The bench gives every cell a (near-)ground-truth in-loop error signal, so a
TARGET_MET stop is correct by the loop's own definition and cannot exhibit a
wrong fixed point -- EXCEPT on W1, where the in-loop signal samples only
N_PLUS_SAMPLES (=8) of a problem's MBPP+ plus_input edge cases. That makes the
in-loop signal a PROXY for the fuller MBPP+ oracle, and the gap between them is
the one genuine wrong-fixed-point instrument extractable from existing data.

Instrument: for each LG trial that CONVERGED (TARGET_MET), re-evaluate its output
against the FULL oracle (base tests + ALL plus_input cases). A converged trial
that fails the full oracle = a confident stop at a wrong fixed point under the
8-sample proxy.

HARDENED (2026-06-07): comparison is value-equality via the canonical solution,
with order-insensitive (multiset) normalization for top-level list/tuple results.
The wrong-fixed-point rate counts NORMALIZED failures (genuine wrong values) only;
strict-but-order-only failures are reported separately as an artifact class (the
earlier repr-based version over-counted these, e.g. Mbpp/255 combinations).

Recompute-only: executes model code already produced during the bench, in an
isolated Docker container. NO model/API spend. Emits data/results/wrong_fixed_point.json.

Run with the bench venv (evalplus + MbppPlus cache):
    cd loopgain-bench && .venv/bin/python analysis/wrong_fixed_point_w1.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
from bench.workloads._shared.sandbox import SandboxExecutionError, ensure_available, execute_worker

WORKER = BENCH / "bench/workloads/_shared/_wfp_exec_worker.py"
OUT = BENCH / "data/results/wrong_fixed_point.json"
W1_FILES = sorted((BENCH / "data/raw").glob("w1-codegen-*-registered.jsonl"))


def extract_code(text):
    m = re.search(r"```(?:python|py)?\s*\n(.*?)\n```", text or "", re.S)
    return m.group(1).strip() if m else (text or "").strip()


def _builtin(name):
    return __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)


def exec_canonical(problem):
    sb = {}
    _builtin("exec")(problem["prompt"] + problem["canonical_solution"], sb)
    return sb


def full_oracle_cases(problem):
    """[(args_repr, expected_repr)] over base_input + ALL plus_input, canonical-derived.

    Drops any input the canonical solution itself can't evaluate (fair bar).
    """
    ep = problem["entry_point"]
    canon = exec_canonical(problem)
    fn = canon[ep]
    cases = []
    for args in (problem.get("base_input") or []) + (problem.get("plus_input") or []):
        try:
            expected = fn(*args)
        except Exception:
            continue
        cases.append((repr(list(args)), repr(expected)))
    return ep, cases


def run_worker(code, entry_point, cases):
    payload = {"code": code, "entry_point": entry_point,
               "cases": [[a, e] for a, e in cases]}
    try:
        return execute_worker(WORKER, payload, 40.0)
    except SandboxExecutionError:
        return {"n_total": len(cases), "strict_fail": len(cases),
                "norm_fail": len(cases), "error": "worker_crash"}


def main():
    ensure_available()
    sys.set_int_max_str_digits(1_000_000)
    from evalplus.data import get_mbpp_plus
    mbpp = get_mbpp_plus()

    cache = {}
    grand = {"converged": 0, "reeval": 0, "passed_full": 0,
             "wfp_hardened": 0, "order_only": 0, "no_oracle": 0, "indeterminate": 0}
    per_cell = {}

    for f in W1_FILES:
        c = {"converged": 0, "reeval": 0, "passed_full": 0,
             "wfp_hardened": 0, "order_only": 0, "no_oracle": 0, "indeterminate": 0, "examples": []}
        for line in f.open():
            r = json.loads(line)
            if r.get("_header"):
                continue
            lg = r["conditions"]["LG"]
            if lg.get("outcome") != "converged":
                continue
            c["converged"] += 1
            name = r["trial_metadata"]["problem_name"]
            if name not in cache:
                prob = mbpp.get(name)
                cache[name] = full_oracle_cases(prob) if prob else (None, [])
            ep, cases = cache[name]
            if not cases:
                c["no_oracle"] += 1
                continue
            c["reeval"] += 1
            res = run_worker(extract_code(lg.get("final_output", "")), ep, cases)
            nt, nf, sf = res["n_total"], res["norm_fail"], res["strict_fail"]
            if res.get("error") is not None or (nt > 0 and nf == nt):
                # Worker timeout/crash OR 100%-fail: a real wrong-fixed-point passed the
                # in-loop proxy (a subset of the full oracle), so it cannot fail 100% of the
                # superset. These are re-eval artifacts (pathological-input timeouts), excluded.
                c["indeterminate"] += 1
            elif nf > 0:
                c["wfp_hardened"] += 1  # genuine: passed proxy, fails 1..n-1 full-oracle cases
                if len(c["examples"]) < 8:
                    c["examples"].append({"problem": name, "norm_fail": nf, "n_total": nt})
            elif sf > 0:
                c["order_only"] += 1  # strict-fail but value-equivalent (order artifact)
            else:
                c["passed_full"] += 1
        per_cell[f.name] = c
        for k in grand:
            grand[k] += c[k]

    denom = grand["reeval"] - grand["indeterminate"]
    rate = (grand["wfp_hardened"] / denom * 100) if denom else 0.0
    out = {
        "_provenance": "aggregate",
        "_description": "Wrong-fixed-point rate on W1: converged (TARGET_MET) LG outputs that "
                        "fail the full MBPP+ oracle (value-equality, order-normalized) despite "
                        "passing the loop's 8-sample proxy. Recompute-only, no model spend.",
        "loopgain_version_under_test": "0.4.0",
        "headline_rate_pct": round(rate, 1),
        "clean_denominator": denom,
        "pooled": {k: grand[k] for k in grand},
        "per_cell": {k: {kk: vv for kk, vv in v.items()} for k, v in per_cell.items()},
    }
    OUT.write_text(json.dumps(out, indent=2))

    print("=" * 72)
    print("W1 WRONG-FIXED-POINT (hardened: value-equality, order-normalized)")
    print("=" * 72)
    for fname, c in per_cell.items():
        cd = c["reeval"] - c["indeterminate"]
        rr = (c["wfp_hardened"] / cd * 100) if cd else 0.0
        print(f"\n{fname}")
        print(f"  converged {c['converged']} | re-evaluable {c['reeval']} (no-oracle {c['no_oracle']})")
        print(f"  passed full {c['passed_full']} | order-only {c['order_only']} | indeterminate(excl) {c['indeterminate']}")
        print(f"  WRONG FIXED POINT (hardened) {c['wfp_hardened']} / {cd} clean  ({rr:.1f}%)")
        for ex in c["examples"]:
            print(f"      e.g. {ex['problem']}: {ex['norm_fail']} genuine fails of {ex['n_total']}")
    print("\n" + "-" * 72)
    print(f"POOLED hardened wrong-fixed-point: {grand['wfp_hardened']}/{denom} clean = {rate:.1f}%")
    print(f"  (indeterminate excluded: {grand['indeterminate']}; order-only: {grand['order_only']}; passed-full {grand['passed_full']})")
    print(f"  written -> {OUT.relative_to(BENCH)}")
    print("-" * 72)


if __name__ == "__main__":
    main()
