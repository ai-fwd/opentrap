# Prompt Injection via HTML

This scenario implements a two-stage data pipeline:

1. Generate realistic base HTML content from a local OpenAI-compatible LLM.
2. Apply canonical HTML prompt-injection attacks after base generation.

## Environment

Set these environment variables (for example in `.env`):

- `OPENAI_API_KEY`
- `OPENAI_URL`
- `OPENAI_MODEL`

`OPENAI_URL` accepts the same forms as the ACME client:

- `https://api.openai.com`
- `https://api.openai.com/v1`
- `https://api.openai.com/v1/responses`

## Generate Data

```bash
uv run opentrap init
uv run opentrap attack perception/prompt_injection_via_html
```

Trap-specific controls are configured in `opentrap.yaml` under:

- `traps.perception/prompt_injection_via_html.base_count` (default `3`)
- `traps.perception/prompt_injection_via_html.location_temperature` in `[0,1]` (default `0`)
- `traps.perception/prompt_injection_via_html.density_temperature` in `[0,1]` (default `0`)
- `traps.perception/prompt_injection_via_html.diversity_temperature` in `[0,1]` (default `0`)

## Output

Artifacts are written to:

- `runs/perception__prompt_injection_via_html/<run_id>/pages/<id>.htm`
- `runs/perception__prompt_injection_via_html/<run_id>/metadata.jsonl`

`metadata.jsonl` maps each file to base/poisoned status and injection details.
