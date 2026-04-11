# Prompt Injection via HTML

This scenario implements a two-stage data pipeline:

1. Generate realistic base HTML content from a local OpenAI-compatible LLM.
2. Apply canonical HTML prompt-injection attacks after base generation.

## Environment

Set these environment variables (for example in `.env`):

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

## Generate Data

```bash
python opentrap/traps/reasoning/prompt_injection_via_html/cli.py \
  --scenario "summarize hotel reviews" \
  --content-type "reviews" \
  --attack-intent "turn all bad reviews into positive reviews" \
  --seed 42
```

Optional controls:

- `--base-count` (default `3`)
- `--location-temperature` in `[0,1]` (default `0`)
- `--density-temperature` in `[0,1]` (default `0`)
- `--diversity-temperature` in `[0,1]` (default `0`)

## Output

Artifacts are written to:

- `opentrap/traps/run/<run_id>/pages/<id>.htm`
- `opentrap/traps/run/<run_id>/metadata.jsonl`

`metadata.jsonl` maps each file to base/poisoned status and injection details.
