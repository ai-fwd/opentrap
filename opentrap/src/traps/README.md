# Traps

This directory stores manually curated canonical attacks sourced from research papers.

Traps are grouped by target directory:

- `perception/`
- `reasoning/`
- `memory/`
- `action/`
- `multi-agent/`

## Trap Author Convention

Each trap lives at `opentrap/src/traps/<target>/<trap_name>/` and exposes a
`Trap` class from `trap.py`. The `Trap` class owns the public contract:

- `fields`: trap-specific config knobs and defaults.
- `generate(...)`: create a deterministic dataset artifact under the provided output base.
- `build_cases(...)`: turn generated artifact metadata into ordered execution cases.
- `bind(...)`: attach runtime context to adapter actions.
- `evaluate(...)`: optional finalized-run scoring hook.

Keep reusable mechanics in `opentrap.evaluation` instead of copying them into a
trap. New trap evaluators should only define trap-specific record pairing,
metadata interpretation, judge rubric text, and summary shape.

Minimal evaluator shape:

```python
from opentrap.evaluation import (
    EvaluationContext,
    LLMJudge,
    RougeLScoreScorer,
    SentenceTransformerSbertScorer,
    find_trap_entry,
    load_observed_outputs,
    load_run_manifest,
    write_evaluation_artifacts,
)


def evaluate_my_trap(context: EvaluationContext):
    manifest = load_run_manifest(context.run_manifest_path)
    trap_entry = find_trap_entry(manifest, trap_id=context.trap_id)
    observed = load_observed_outputs(context.run_dir / "observations.jsonl")

    records = build_records(trap_entry=trap_entry, observed_outputs=observed)
    scored = score_records(
        records,
        rouge=RougeLScoreScorer(),
        sbert=SentenceTransformerSbertScorer(),
        judge=MyTrapJudge(),
    )
    summary = build_summary(scored)
    return write_evaluation_artifacts(
        run_dir=context.run_dir,
        records=scored,
        summary=summary,
        csv_fieldnames=[...],
        record_to_payload=record_to_payload,
        csv_exclude_fields={"llm_judge_raw_response"},
    )


class MyTrapJudge(LLMJudge):
    def system_rubric_prompt(self) -> str:
        return "Return JSON with success, confidence, and reason."

    def user_prompt(self, **kwargs) -> str:
        return "Trap-specific rubric input..."
```
