from __future__ import annotations

from adapter.host import UpstreamSpec


def get_upstreams() -> list[UpstreamSpec]:
    """Return generated named upstream declarations consumed by route forwarding."""
    return []
