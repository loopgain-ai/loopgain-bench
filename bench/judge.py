"""LLM-judge for pairwise quality comparison.

Per BENCH_PROTOCOL.md methodology lockdown #2: judge model ≠ loop model.
Per lockdown #3: pairwise position is randomized (LoopGain output in position
A in 50% of comparisons, B in the other 50%), seeded deterministically per
trial so the comparison is reproducible.

This module exposes one entry point: `pairwise_winrate`. It takes two
collections of (trial_id, output) pairs — one for LoopGain, one for B20 —
runs each pair through a judge, and reports the proportion of comparisons
where LoopGain's output is preferred (ties = 0.5).

Per lockdown #9 (raw data immutable): each comparison is persisted to
`data/raw/judge-<workload>-<tag>.jsonl` as it is decided, so re-analysis
reads the same judgments. Re-running the judge with the same tag overwrites
that file; to keep a prior set, use a different tag.

The judge prompt is INTENTIONALLY single-shot and short — long judge prompts
increase variance. The cross-vendor rotation (Anthropic ↔ OpenAI) is what
mitigates judge bias, not a sophisticated prompt.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .llm import client_for_model


@dataclass(frozen=True)
class PairwiseComparison:
    trial_id: str
    output_a: str  # the output placed in position A (randomized)
    output_b: str
    lg_position: str  # 'A' or 'B' — which position LoopGain was in
    judge_choice: str  # 'A', 'B', or 'TIE'
    judge_model: str
    judge_rationale: str  # brief, for spot-checking; never used in aggregate metrics


JUDGE_PROMPT_TEMPLATE = """You are evaluating two attempts at the same task. Read both carefully, then output ONE of: A, B, or TIE.

TASK:
{task_description}

ATTEMPT A:
{output_a}

ATTEMPT B:
{output_b}

Output exactly one letter (A, B, or TIE) followed by one sentence of reasoning. No preamble."""


def pairwise_winrate(
    lg_outputs: dict[str, str],
    b20_outputs: dict[str, str],
    task_description: str,
    judge_model: str,
    *,
    loop_model: str,
    workload_id: str = "unknown",
    tag: str = "untagged",
    raw_dir: Optional[Path] = None,
    seed: int = 0,
) -> dict:
    """Run pairwise judgments and return aggregate stats.

    Args:
        lg_outputs:  {trial_id: output_string} for LoopGain condition
        b20_outputs: {trial_id: output_string} for B20 condition
        task_description: one-line description of what the task was (for judge context)
        judge_model: model id; MUST be different from the loop model. Enforced here.
        loop_model: model id used in the loop (cross-model check input)
        workload_id: cell id; used to name the persisted judge JSONL
        tag: run tag (e.g. "registered"); also part of the judge JSONL name
        raw_dir: directory to write judge-*.jsonl into; defaults to data/raw/
        seed: deterministic position-randomization seed

    Returns:
        {
            "n_comparisons": int,
            "lg_wins": int,
            "b20_wins": int,
            "ties": int,
            "winrate_lg": float,  # (lg_wins + 0.5 * ties) / n
            "judge_model": str,
            "comparisons": list[PairwiseComparison],  # for raw audit
        }
    """
    _enforce_cross_model(loop_model, judge_model)

    common_ids = sorted(set(lg_outputs) & set(b20_outputs))

    if raw_dir is None:
        raw_dir = Path(__file__).resolve().parents[1] / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = raw_dir / f"judge-{workload_id}-{tag}.jsonl"

    if not common_ids:
        # Still write a header so downstream readers see the intent.
        with jsonl_path.open("w") as f:
            f.write(json.dumps({
                "_header": True,
                "workload_id": workload_id,
                "tag": tag,
                "loop_model": loop_model,
                "judge_model": judge_model,
                "n_planned": 0,
                "seed": seed,
                "note": "no trial_ids in common between lg and b20 outputs",
            }) + "\n")
        return {
            "n_comparisons": 0,
            "lg_wins": 0,
            "b20_wins": 0,
            "ties": 0,
            "winrate_lg": float("nan"),
            "judge_model": judge_model,
            "comparisons": [],
        }

    judge_client = client_for_model(judge_model, seed=seed)
    pos_rng = random.Random(seed)

    comparisons: list[PairwiseComparison] = []
    lg_wins = b20_wins = ties = 0

    with jsonl_path.open("w") as f:
        f.write(json.dumps({
            "_header": True,
            "workload_id": workload_id,
            "tag": tag,
            "loop_model": loop_model,
            "judge_model": judge_model,
            "n_planned": len(common_ids),
            "seed": seed,
        }) + "\n")

        for trial_id in common_ids:
            lg_out = lg_outputs[trial_id]
            b20_out = b20_outputs[trial_id]

            lg_in_a = pos_rng.random() < 0.5
            if lg_in_a:
                output_a, output_b, lg_position = lg_out, b20_out, "A"
            else:
                output_a, output_b, lg_position = b20_out, lg_out, "B"

            prompt = JUDGE_PROMPT_TEMPLATE.format(
                task_description=task_description,
                output_a=output_a,
                output_b=output_b,
            )
            try:
                comp = judge_client.call(prompt, max_tokens=200)
                judge_choice, rationale = _parse_judge_response(comp.text)
            except Exception as exc:  # noqa: BLE001 — lockdown #5: flag, don't drop
                judge_choice = "TIE"
                rationale = f"[judge error: {exc!r}]"

            if judge_choice == "TIE":
                ties += 1
            elif (judge_choice == "A" and lg_in_a) or (judge_choice == "B" and not lg_in_a):
                lg_wins += 1
            else:
                b20_wins += 1

            record = PairwiseComparison(
                trial_id=trial_id,
                output_a=output_a,
                output_b=output_b,
                lg_position=lg_position,
                judge_choice=judge_choice,
                judge_model=judge_model,
                judge_rationale=rationale,
            )
            comparisons.append(record)

            # Persist per lockdown #9. Keep outputs in the JSONL for full
            # reproducibility — a future re-judge can be diff'd against this.
            f.write(json.dumps(asdict(record)) + "\n")

    n = len(comparisons)
    winrate_lg = (lg_wins + 0.5 * ties) / n if n > 0 else float("nan")

    return {
        "n_comparisons": n,
        "lg_wins": lg_wins,
        "b20_wins": b20_wins,
        "ties": ties,
        "winrate_lg": winrate_lg,
        "judge_model": judge_model,
        "comparisons": comparisons,
    }


def _parse_judge_response(text: str) -> tuple[str, str]:
    """Extract 'A' | 'B' | 'TIE' from the judge's reply.

    The prompt instructs 'one letter followed by one sentence'. Be tolerant
    of leading whitespace, punctuation, or boilerplate that some models prepend.
    Anything we cannot classify is recorded as TIE with the raw text preserved
    in the rationale for audit.
    """
    raw = (text or "").strip()
    if not raw:
        return "TIE", "[empty judge response]"
    head = raw.lstrip().upper()
    # Strip common leading markers like "Answer:" or "**A**"
    for marker in ("ANSWER:", "RESULT:", "VERDICT:", "CHOICE:"):
        if head.startswith(marker):
            head = head[len(marker):].lstrip()
    head = head.lstrip("*_`# ").lstrip()
    if head.startswith("TIE"):
        return "TIE", raw
    # Match a bare 'A' or 'B' followed by non-letter (so 'Attempt' doesn't match A)
    if head[:1] == "A" and (len(head) == 1 or not head[1].isalpha()):
        return "A", raw
    if head[:1] == "B" and (len(head) == 1 or not head[1].isalpha()):
        return "B", raw
    return "TIE", f"[unparseable] {raw}"


def _enforce_cross_model(loop_model: str, judge_model: str) -> None:
    """Lockdown #2: judge model MUST differ from loop model. Raise loudly."""
    if loop_model == judge_model:
        raise ValueError(
            f"Methodology violation: judge model {judge_model!r} == loop model. "
            f"BENCH_PROTOCOL.md methodology lockdown #2 forbids same-model judging."
        )
    # Same vendor family is also flagged but allowed with a warning
    # (e.g. Sonnet judging Haiku is suboptimal but not invalid).
    if loop_model.startswith("claude-") and judge_model.startswith("claude-"):
        print(
            f"  [judge warning] both loop ({loop_model}) and judge ({judge_model}) "
            f"are Anthropic family. Prefer cross-vendor."
        )
    if (loop_model.startswith("gpt-") or loop_model.startswith("o")) and (
        judge_model.startswith("gpt-") or judge_model.startswith("o")
    ):
        print(
            f"  [judge warning] both loop ({loop_model}) and judge ({judge_model}) "
            f"are OpenAI family. Prefer cross-vendor."
        )
