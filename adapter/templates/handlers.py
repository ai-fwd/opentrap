"""Generated route handlers live in this module.

Naming convention:
- intercept handlers: `intercept_<route_name_normalized>`
- observe handlers: `observe_<route_name_normalized>`

Examples:
    async def intercept_load_document(ctx: RequestContext) -> Response: ...
    async def observe_agent_run(ctx: RequestContext, snapshot: Response) -> None: ...

Notes:
- Use the trap's documented `ctx.trap_actions` for trap-backed content access.
- Do not emit events from handlers; runtime emits route and observer events.

Helpful RequestContext methods:
- ctx.path_param(name, required=True)
- await ctx.json_body()
- await ctx.body_text()
- await ctx.body_bytes()
"""
from __future__ import annotations
