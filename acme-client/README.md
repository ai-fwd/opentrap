# Acme Client Inbox Assistant

A lightweight inbox assistant demo.

## What It Does

- Shows a 20-email inbox UI
- Opens email details with rendered HTML preview
- Runs AI actions via OpenAI Responses API:
  - Summarize Email
  - Summarize Inbox
  - Extract Actions
  - Draft Reply

## Stack

- Bun + TypeScript backend
- React + Vite + Tailwind frontend (Lovable UI stack)
- Existing backend API contract preserved:
  - `GET /api/inbox`
  - `GET /api/inbox/:id`
  - `POST /api/assist`

## Setup

1. Install dependencies

```bash
bun install
```

2. Configure env

```bash
cp ../.env.shared.example ../.env.shared
cp .env.example .env
# set OPENAI_API_KEY in ../.env.shared (or override in .env)
```

3. Run dev

```bash
bun run dev
```

- API: `http://localhost:3000`
- UI: `http://localhost:5173`

## Build + Start

```bash
bun run build
bun run start
```

Open `http://localhost:3000`.

## Fixtures

This app now uses committed static fixtures only:
- `fixtures/inbox.json`
- `fixtures/emails/*.html`

Notes:
- All email HTML fixtures are stored as full documents (`<!doctype html><html>...`).
- No `/data` bootstrap pipeline is required.
- No runtime fixture-generation script is required.

## Environment Variables

- Shared: `../.env.shared` (loaded first)
- Project: `./.env` (loaded second; overrides shared file values)
- `OPENAI_API_KEY` required
- `OPENAI_MODEL` optional (default `gpt-4.1-mini`)
- `OPENAI_URL` optional base URL (default `https://api.openai.com`; app uses `${OPENAI_URL}/v1/responses`)
- `INBOX_UPSTREAM_BASE_URL` optional base URL for internal inbox upstream HTTP calls (default `http://127.0.0.1:${PORT}`)
- `PORT` optional (default `3000`)
