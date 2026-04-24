# OpenTrap Adapter Contract

## Purpose

Define the adapter as the implementation-agnostic boundary that presents trap data to the product, preserves normal product behavior, and records evidence for the run.

The adapter host is HTTP-first. For a non-HTTP app, product, or service, expose an equivalent HTTP facade in the adapter and bridge from that facade into the real runtime.

The product-under-test is out of bounds. Agents should not modify the product or service being tested.

Adapter implementation work should only modify generated output files:
- `adapter/generated/default/adapter.yaml`
- `adapter/generated/default/handlers.py`

Generated `adapter.yaml` shape:
- `upstreams`: mapping of `upstream_key: base_url`
- `routes`: list of route declarations with `name`, `path`, `methods`, `mode`, optional `upstream`, optional `upstream_path`

Use `adapter/templates/` as read-only references.

## Core Concepts

- `intercept`: the adapter owns the response and usually serves trap-backed content into the product's normal ingress path.
- `passthrough`: the adapter forwards the request unchanged to a named upstream and adds no trap logic.
- `observe`: the adapter forwards to the real upstream, then records side effects or evidence without changing the client-visible response.

## Upstreams

- Declare every real downstream dependency in generated `adapter.yaml` under the `upstreams` mapping.
- `passthrough` and `observe` routes should point at those named upstream keys instead of hardcoding destinations in generated handlers.
- Use `passthrough` when the adapter should preserve the upstream response exactly.
- Use `observe` when the adapter should preserve the upstream response exactly but also capture evidence about the downstream execution.
- `intercept` routes do not use an upstream because the adapter owns the response.

## Authoring Workflow

- Start from `adapter/authoring_worksheet.md` before writing generated code.
  - Complete one worksheet per trap, because route inventory is trap-specific.
  - Save the completed worksheet under `adapter/generated/default/` with a trap specific name.

- Discover routes from the exercised user task, not by cloning the product’s full API surface.
- Start with scenario tests, integration tests, and e2e tests that demonstrate the task OpenTrap is trying to run.
- If tests are absent or incomplete, inspect product routers, entrypoints, outbound HTTP - clients, SDK wrappers, and configuration that controls provider or base URL selection.
- Treat tests as the best evidence of which routes are actually needed, but do not require tests to exist.

- After per-trap review, consolidate the resulting routes and upstreams into `adapter.yaml` for that product:
  - merge identical routes
  - merge identical upstream declarations
- keep trap-specific handler logic in `handlers.py` behind the final consolidated route map
- Inventory the real user task end to end before defining routes.
- Identify the product's:
  - content ingress routes
  - supporting or unrelated routes
  - execution or side-effect routes
- Classify them:
  - content ingress -> `intercept`
  - untouched support routes -> `passthrough`
  - model, agent, tool, or action boundaries -> `observe`
- Hard rule: if a user task reads trapped content and then triggers reasoning or an external action, the adapter is incomplete unless it defines both route families.

- Only add adapter routes that are necessary for the product-under-test to execute the trap scenario.
- Do not add routes solely because they exist in the product.
- Routes not exercised by the scenario may be ignored.
- Every added route must be justified as one of:
  - content ingress
  - downstream execution or side effect
  - required support path without which the scenario cannot run

- Generic example:
  - `GET /documents/{id}` -> `intercept`
  - `POST /agent/run` -> `observe`
  - `GET /healthz` -> `passthrough`

## Route Classification Table

| Mode | Use for | Generic examples |
| --- | --- | --- |
| `intercept` | Serve trap-backed inputs into the product's normal read path | document fetch, search results, file load, message fetch, knowledge retrieval, session history load |
| `passthrough` | Preserve unrelated or support behavior unchanged | auth, config, health, unrelated CRUD, static support APIs |
| `observe` | Watch downstream execution after the product consumes trapped content | model inference, agent run, tool execution, webhook dispatch, job enqueue, outbound message send, action commit |

## Trap Summary: `perception/prompt_injection_via_html`

- Purpose: generate HTML documents that look normal to the product but contain hidden prompt-injection payloads intended to influence downstream reasoning or actions after the product ingests the content.
- What the adapter must do for this trap:
  - identify the product routes where HTML or document content enters the product's normal read path and expose those ingress routes as `intercept` routes that serve trap-backed content 
  - identify the downstream execution boundary triggered after the product reads that content, such as model inference, agent run, tool execution, or outbound action and expose those execution routes as `observe` routes so the upstream response remains unchanged while evidence is captured
  - for OpenAI Responses API observe routes, implement generated `observe_openai_responses(...)` as a thin wrapper that delegates to `opentrap.adapter.default_handlers.observe_openai_responses_default(...)`

### Trap Action Surface (`perception/prompt_injection_via_html`)

- `ctx.trap_actions.get_current_data() -> str`
- Do not assume additional trap action methods unless they are explicitly documented.

## Handler Rules

- Never hardcode trap file paths; use `ctx.trap_actions`.
- `intercept` handlers return product-shaped responses.
- `observe` handlers must not mutate the forwarded response and should delegate to shared default helpers when available.
- `passthrough` routes should rely on named upstream declarations in generated `adapter.yaml`; keep forwarding logic out of generated handlers.
- Use `request_id` for correlation in adapter-generated errors or logs.
- Missing trap data should produce controlled 4xx-style responses, not crashes.
- Handlers must not emit evidence events directly; runtime emits standardized route lifecycle and observer events.
