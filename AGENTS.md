# OpenTrap Agent Operating Context

This file is a durable context anchor for future chats and agent sessions.

## Always Load First

1. `README.md`
2. `AGENTS.md`

## Non-Negotiable Boundaries

- Keep three top-level components:
  - `acme-client/` (product-under-test in TypeScript)
  - `opentrap/` (Python red-team CLI)
  - `adapter/` (implementation-agnostic contract assets)
- `acme-client` must remain product-like and must not be aware of the red-team internals.
- Adapter contract uses `adapter/adapter.md`, JSON Schemas, vectors, and conformance tests.
- Trap assets live in `opentrap/traps/`.
- Keep only these initial automated tests:
  - `opentrap/` unit tests
  - `adapter/` contract tests

## Tooling Defaults

- Python: `uv`, `ruff`, `pytest`
- TypeScript: Bun + strict TypeScript + `bun:test`
- All dependency declarations must use exact versions (no `^`, `~`, `>=`, or wildcard ranges).
- Commit and maintain lockfiles (`uv.lock`, `acme-client/bun.lock`); dependency upgrades require explicit user approval.
