# OpenTrap

Turn AI attack research into runnable product tests without changing application code. 

## What Is OpenTrap?

> [!IMPORTANT]
> OpenTrap is a work in progress. It currently includes one end to end trap: `perception/prompt_injection_via_html`.
>
> The current goal is to validate the approach and collect feedback before expanding the trap catalog.

OpenTrap is a CLI-first security testing toolkit for agent and LLM applications. A trap is a runnable implementation of an attack pattern from research that can be customized for a specific product scenario. The adapter places that trap into a real product workflow, observes what the product does, and evaluates the results to show whether the trap succeeded.

## Quick Demo

The built-in demo runs the _[Acme Client](.github/assets/acme-client.png)_ inbox assistant end to end with simulated trap data and model responses. The demo showcases prompt injection via HTML with the goal to "change email sentiment from positive to negative".


```bash
uv sync --group dev --frozen
uv run opentrap demo
```

![OpenTrap demo CLI](.github/assets/demo/demo.gif)

Here's what's happening behind the scenes:

1. Loads one clean email and one poisoned HTML email from `demo/trap-data/`.
2. Starts the OpenTrap adapter on `127.0.0.1:7860`.
3. Runs Acme's normal Playwright harness.
4. Observes the model response.
5. Evaluates the clean and poisoned outputs.
6. Writes a report under `runs/<run_id>/`.

![OpenTrap demo report preview](.github/assets/demo/demo-report.png)

Open the [full report](https://htmlpreview.github.io/?https://github.com/ai-fwd/opentrap/blob/master/.github/assets/demo/evaluation_report.html) and inspect all run and evaluation artifacts under `.github/assets/demo/`

## How It Works

OpenTrap is designed so the product under test keeps its normal code path. In most cases, you change configuration rather than application code.

For an LLM-backed app, that usually means pointing model calls at the local OpenTrap adapter.

Adapters support three route modes:

| Mode          | Purpose                                                                               |
| ------------- | ------------------------------------------------------------------------------------- |
| `intercept`   | Serve trap-backed content into the product's normal read path.                        |
| `passthrough` | Forward unrelated support traffic unchanged.                                          |
| `observe`     | Record evidence from model, tool, or action boundaries. |

Visually:

```mermaid
flowchart LR
    product["Product (under test)"]

    adapter["OpenTrap adapter<br/>127.0.0.1:7860"]

    trap["Serve active trap data"]

    upstream["Upstream service"]

    evidence["Record evidence"]

    product -->|"request for<br/>content or LLM"| adapter

    adapter -->|"intercept"| trap
    adapter -->|"passthrough"| upstream

    trap -->|"response"| adapter
    upstream -->|"response"| adapter

    adapter -->|"response"| product
    adapter -.->|"observe"| evidence
```

Importantly, the product should still believe it is loading normal content and calling its normal model provider. OpenTrap's adapter is custom built (see the [adapter contract](adapter/adapter.md)) for the product under test and sits at the specific boundaries required by the trap.



## Getting Started

### Start with initializing OpenTrap:

```bash
uv run opentrap init
```

OpenTrap will prompt for context on the product under test, the type of content it needs, and what the traps should be testing for. These are used for generating the base and variants data items.

| Prompt | Example answer |
| --- | --- |
| Describe your product | An email inbox interface where the user can read emails they have received |
| What type of content does your product use? | Emails from coworkers and clients |
| What is the trap's intent? | Change email sentiment from positive to negative |
| Seed (optional integer) | 42 |
| What command runs your test suite? | bun run test:e2e |
| Where should this command be run? (relative path) | acme-client |


### Add data samples 

Include some data samples under `.opentrap/samples` to help guide data generation. This is optional but **strongly** encouraged.

### Create the adapter

Point your favourite agentic coding tool at this repo, then use this prompt:

```
Read adapter.md and follow its instructions. The product under test is located at [path to your product]
```

### Verify setup

1. `.opentrap` dir with a `opentrap.yaml` configuration file
2. `adapter/generated/<product_under_test>/adapter.yaml` configuration file that defines the various routes and upstreams
3. `adapter/generated/<product_under_test>/handlers.py` intercept and observation handlers

### Product configuration changes

At a minimum you'll need to update the URL used to call your LLM and any other service involved in retrieving the data required to exercise the given scenario. For example, here's what acme-client requires when using a local LLM:

```bash
OPENAI_API_KEY=sk-dummy-key
OPENAI_MODEL=default
OPENAI_URL=http://127.0.0.1:7860
INBOX_UPSTREAM_BASE_URL=http://127.0.0.1:7860
```

### Run OpenTrap

```bash
uv run opentrap run perception/prompt_injection_via_html
```

### Review the run and evaluation artifacts

Locate the run under the `runs/<run-id>` dir.


## CLI Commands

```bash
uv run opentrap list
```

For a configured project, the normal full run is:

```bash
uv run opentrap run perception/prompt_injection_via_html
```

You can also split the pipeline:

```bash
uv run opentrap generate perception/prompt_injection_via_html
uv run opentrap execute perception/prompt_injection_via_html
uv run opentrap eval latest
```

## Core Concepts

OpenTrap has a few moving parts:

| Concept            | Meaning                                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------------- |
| Product under test | The app being evaluated. In this repo, that is `acme-client/`, a TypeScript inbox assistant.             |
| Trap               | A runnable implementation of an attack pattern from research.                                            |
| Adapter            | The boundary OpenTrap uses to serve trap data into the product and observe model, tool, or action calls. |
| Case               | One concrete trap input served during a run. A trap can include clean base cases and poisoned variants.  |
| Evidence           | Runtime artifacts captured while the product interacts with the trap.                                    |
| Evaluation         | Trap-owned scoring logic that decides whether the trap achieved its intended effect.                     |
| Verdict            | The run-level security result derived from the trap evaluation.                                          |


## Repository Layout

- `acme-client/`: product under test.
- `opentrap/`: Python CLI, adapter runtime, trap loading, execution, and evaluation.
- `adapter/`: implementation-agnostic adapter contract and templates.
- `opentrap/src/traps/`: trap implementations grouped by target.
- `demo/`: demo assets.

## Trap Catalog

| Target | Trap | Status | Credit | Demo |
| --- | --- | --- | --- | --- |
| Perception | HTML prompt injection | Working | [Verma & Yadav (2025) — Decoding Latent Attack Surfaces in LLMs: Prompt Injection via HTML in Web Summarization](https://arxiv.org/abs/2509.05831) | yes |

## Reports and Artifacts

| File                      | Purpose                                                            |
| ------------------------- | ------------------------------------------------------------------ |
| `report.json`             | Run-level verdict, counts, and security result.                    |
| `evaluation_summary.json` | Aggregate scoring metrics and trap-specific breakdowns.            |
| `evaluation.jsonl`        | One scored record per evaluated poisoned case.                     |
| `evaluation.csv`          | Spreadsheet-friendly evaluation results.                           |
| `observations.jsonl`      | Observed model, tool, or action outputs captured by the adapter.   |
| `traces.jsonl`            | Route-level adapter dispatch evidence.                             |
| `evaluation_report.html`  | Human-readable report with verdict, breakdowns, and case evidence. |


## Developing OpenTrap

Install and run the Python checks:

```bash
uv sync --group dev --frozen
uv run ruff check
uv run python -m pytest
```

Acme uses Bun:

```bash
cd acme-client
bun install
bun run test:e2e
```

Dependency declarations are exact-versioned and lockfiles are committed.




