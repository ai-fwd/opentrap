# Prompt Injection via HTML

This scenario implements a two-stage data pipeline:

1. Generate realistic base HTML content from a local OpenAI-compatible LLM.
2. Apply canonical HTML prompt-injection attacks after base generation.

## Environment

Set these environment variables in `opentrap/.env`:

- `OPENAI_API_KEY`
- `OPENAI_URL`
- `OPENAI_MODEL`

## Generate Data

```bash
uv run opentrap init
uv run opentrap perception/prompt_injection_via_html
```

Trap-specific controls are configured in `.opentrap/opentrap.yaml` under:

- `traps.perception/prompt_injection_via_html.base_count` (default `3`)
- `traps.perception/prompt_injection_via_html.location_temperature` in `[0,1]` (default `0`)
- `traps.perception/prompt_injection_via_html.density_temperature` in `[0,1]` (default `0`)
- `traps.perception/prompt_injection_via_html.diversity_temperature` in `[0,1]` (default `0`)

## Output

Artifacts are written to:

- `.opentrap/dataset/perception/prompt_injection_via_html/<dataset_fingerprint>/data/<id>.htm`
- `.opentrap/dataset/perception/prompt_injection_via_html/<dataset_fingerprint>/metadata.jsonl`

`metadata.jsonl` maps each file to base/poisoned status and injection details.
