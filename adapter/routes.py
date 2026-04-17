from __future__ import annotations

from adapter.host import RouteSpec


def get_routes() -> list[RouteSpec]:
    """Return trap-specific route declarations consumed by the fixed adapter host."""
    return []
