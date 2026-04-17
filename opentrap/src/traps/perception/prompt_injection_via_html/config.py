from __future__ import annotations

from dataclasses import dataclass

from opentrap.trap_contract import SampleBoundary


@dataclass(frozen=True)
class GenerationConfig:
    scenario: str
    content_style: str
    trap_intent: str
    location_temperature: float = 0.0
    density_temperature: float = 0.0
    diversity_temperature: float = 0.0
    seed: int | None = None
    base_count: int = 3
    run_id: str | None = None
    samples: tuple[SampleBoundary, ...] = ()


def validate_temperature(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1, got {value}")
    return value


def build_generation_config(
    *,
    scenario: str,
    content_style: str,
    trap_intent: str,
    location_temperature: float,
    density_temperature: float,
    diversity_temperature: float,
    seed: int | None,
    base_count: int,
    run_id: str | None,
    samples: tuple[SampleBoundary, ...] = (),
) -> GenerationConfig:
    if base_count < 1:
        raise ValueError("base_count must be >= 1")
    return GenerationConfig(
        scenario=scenario,
        content_style=content_style,
        trap_intent=trap_intent,
        location_temperature=validate_temperature("location_temperature", location_temperature),
        density_temperature=validate_temperature("density_temperature", density_temperature),
        diversity_temperature=validate_temperature("diversity_temperature", diversity_temperature),
        seed=seed,
        base_count=base_count,
        run_id=run_id,
        samples=samples,
    )
