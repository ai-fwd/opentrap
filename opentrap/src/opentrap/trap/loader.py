from __future__ import annotations

from pathlib import Path

from opentrap.trap.registry import TrapRegistry, TrapRegistryError, build_trap_registry


def load_registry_from_candidates(candidate_dirs: tuple[Path, ...]) -> TrapRegistry | None:
    """Load trap registry from candidate directories in priority order.

    Returns:
        First successfully loaded TrapRegistry, or None when no candidate directory exists.

    Raises:
        TrapRegistryError: At least one candidate directory existed but all valid candidates failed.
    """
    last_error: TrapRegistryError | None = None
    for traps_dir in candidate_dirs:
        if not traps_dir.exists() or not traps_dir.is_dir():
            continue
        try:
            return build_trap_registry(traps_dir)
        except TrapRegistryError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error

    return None
