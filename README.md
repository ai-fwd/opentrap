# OpenTrap

OpenTrap is an open-source tool for turning AI attack research into runnable scenarios.

Inspiration comes from the Google DeepMind [AI Agent Traps](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6372438) paper which maps new attack surfaces emerging as agents interact with users, other agents, and external resources. These attacks manipulate the environment around the agent, using content, context, and system dynamics to steer behaviour in unintended ways.

OpenTrap takes attacks described in academic work and turns them into runnable, reproducible scenarios. Given a simple description of your product, it generates realistic environments and adversarial inputs tailored to your use case.

The goal is to make these attack classes easy to apply in practice, with a lightweight adapter that lets you run your system against them end to end, without needing to rethink your existing code or workflow.

## Repository Layout

- `acme-client/`: TypeScript product-under-test (Bun + strict TypeScript + bun:test).
- `opentrap/`: Python engine (CLI-first, production-quality defaults with uv + ruff + pytest).
- `adapter/`: Implementation-agnostic adapter contract (`adapter.md`, and conformance harness).
- `opentrap/src/traps/`: Manually curated attack scenarios grouped by target subdirectory.

## OpenTrap CLI

Traps are discovered dynamically from `opentrap/src/traps/<target>/<trap_name>`.

Pipeline mental model:

- `run` = generate dataset + execute harness + evaluate results
- `generate` = create or reuse trap dataset only
- `execute` = run configured harness against cached trap cases
- `eval` = score an existing finalized run

Core commands:

```bash
# List all traps
uv run opentrap list

# List traps in one target
uv run opentrap list --target reasoning

# Initialize project + trap-specific config
uv run opentrap init

# Full end-to-end run (generate + execute + eval)
uv run opentrap run perception/prompt_injection_via_html

# Generate/reuse dataset only
uv run opentrap generate perception/prompt_injection_via_html

# Force dataset regeneration (ignore existing cache)
uv run opentrap generate perception/prompt_injection_via_html --force

# Execute harness only (requires cached dataset)
uv run opentrap execute perception/prompt_injection_via_html

# Evaluate most recent finalized run
uv run opentrap eval latest

# Evaluate a specific run id
uv run opentrap eval <run_id>
```

Case limiting:

```bash
# Limit executed+evaluated cases for full run
uv run opentrap run perception/prompt_injection_via_html --max-cases 5

# Limit executed cases when running harness-only
uv run opentrap execute perception/prompt_injection_via_html --max-cases 5

# Limit scored cases when evaluating an existing run
uv run opentrap eval latest --max-cases 5
```

## Sample Boundaries

Drop optional representative examples under:

```bash
.opentrap/samples/
```

Any supported text-like file in this directory tree is ingested and provided to traps as examples.

## Quickstart (OpenTrap CLI)

```bash
uv sync --group dev --frozen
uv run python -m pytest
```
