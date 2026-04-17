from __future__ import annotations

"""Generated route handlers live in this module.

Add async intercept handlers as:
    async def name(ctx: RequestContext) -> Response: ...

Add async observe handlers as:
    async def name(ctx: RequestContext, snapshot: Response) -> None: ...
"""

from adapter.host import RequestContext, ManifestTrapView
from fastapi import Response
