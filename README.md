# OpenTrap

OpenTrap is an open-source, flexible red teaming scenario testing tool focused on canonical attacks from published research papers.

## Repository Layout

- `acme-client/`: TypeScript product-under-test (Bun + strict TypeScript + bun:test).
- `opentrap/`: Python red-team engine (CLI-first, production-quality defaults with uv + ruff + pytest).
- `adapter/`: Implementation-agnostic adapter contract (`adapter.md`, and conformance harness).
- `opentrap/traps/`: Manually curated attack scenarios grouped by target subdirectory.

## OpenTrap CLI

Targets are fixed to:

- `perception`
- `reasoning`
- `memory`
- `action`
- `multi-agent`

Commands:

```bash
# List all traps
uv run opentrap list

# List traps in one target
uv run opentrap list --target reasoning

# Run all traps
uv run opentrap attack

# Run a single trap by target/name
uv run opentrap attack reasoning/chain-trap

# Optional custom report path
uv run opentrap attack reasoning/chain-trap --output runs/custom.json
```

## Quickstart (OpenTrap CLI)

```bash
uv sync --group dev --frozen
uv run pytest
```

## Quickstart (ACME Client)

```bash
cd acme-client
bun install
bun test
bun run typecheck
```
