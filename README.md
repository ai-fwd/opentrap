# OpenTrap

OpenTrap is an open-source tool for turning AI attack research into runnable scenarios.

Inspiration comes from the Google DeepMind [AI Agent Traps](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6372438) paper which maps new attack surfaces emerging as agents interact with users, other agents, and external resources. These attacks manipulate the environment around the agent, using content, context, and system dynamics to steer behaviour in unintended ways.

OpenTrap takes attacks described in academic work and turns them into runnable, reproducible scenarios. Given a simple description of your product, it generates realistic environments and adversarial inputs tailored to your use case.

The goal is to make these attack classes easy to apply in practice, with a lightweight adapter that lets you run your system against them end to end, without needing to rethink your existing code or workflow.

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
