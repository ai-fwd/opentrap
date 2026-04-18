from __future__ import annotations

from opentrap.adapter import UpstreamSpec


def get_upstreams() -> list[UpstreamSpec]:
    """Return generated named upstream declarations consumed by route forwarding."""
    return []
