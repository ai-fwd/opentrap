from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentrap.trap_contract import MISSING_DEFAULT, SharedConfig, TrapFieldSpec, TrapSpec


class AttackConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedAttackConfig:
    shared: SharedConfig
    trap_configs: dict[str, dict[str, Any]]


def _yaml_module():
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise AttackConfigError(
            "PyYAML is required. Install dependencies before running this command."
        ) from exc
    return yaml


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _validate_shared_config(raw: Mapping[str, Any]) -> SharedConfig:
    allowed_keys = {"scenario", "content_type", "attack_intent", "seed"}
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise AttackConfigError(f"shared has unknown key(s): {', '.join(unknown_keys)}")

    values: dict[str, str] = {}
    for field_name in ("scenario", "content_type", "attack_intent"):
        value = raw.get(field_name)
        if not isinstance(value, str):
            raise AttackConfigError(f"shared.{field_name} must be a string")
        if not value.strip():
            raise AttackConfigError(f"shared.{field_name} cannot be empty")
        values[field_name] = value

    seed_raw = raw.get("seed")
    if seed_raw is None:
        seed = None
    elif _is_integer(seed_raw):
        seed = int(seed_raw)
    else:
        raise AttackConfigError("shared.seed must be an integer or null")

    return SharedConfig(
        scenario=values["scenario"],
        content_type=values["content_type"],
        attack_intent=values["attack_intent"],
        seed=seed,
    )


def _validate_field_constraints(field_name: str, value: Any, spec: TrapFieldSpec) -> Any:
    if spec.type == "string":
        if not isinstance(value, str):
            raise AttackConfigError(f"{field_name} must be a string")
        if spec.min_length is not None and len(value) < spec.min_length:
            raise AttackConfigError(f"{field_name} must be at least {spec.min_length} characters")
        validated_value: Any = value
    elif spec.type == "integer":
        if not _is_integer(value):
            raise AttackConfigError(f"{field_name} must be an integer")
        validated_value = int(value)
    elif spec.type == "number":
        if not _is_number(value):
            raise AttackConfigError(f"{field_name} must be a number")
        validated_value = float(value)
    elif spec.type == "boolean":
        if not isinstance(value, bool):
            raise AttackConfigError(f"{field_name} must be a boolean")
        validated_value = value
    else:
        raise AttackConfigError(f"{field_name} has unsupported type '{spec.type}'")

    if spec.min is not None and _is_number(validated_value) and validated_value < spec.min:
        raise AttackConfigError(f"{field_name} must be >= {spec.min}")

    if spec.max is not None and _is_number(validated_value) and validated_value > spec.max:
        raise AttackConfigError(f"{field_name} must be <= {spec.max}")

    if spec.allowed_values is not None and validated_value not in spec.allowed_values:
        allowed = ", ".join(repr(value) for value in spec.allowed_values)
        raise AttackConfigError(f"{field_name} must be one of: {allowed}")

    return validated_value


def _validate_trap_config(trap_id: str, raw: Mapping[str, Any], spec: TrapSpec) -> dict[str, Any]:
    unknown_keys = sorted(set(raw) - set(spec.fields))
    if unknown_keys:
        raise AttackConfigError(
            f"traps.{trap_id} has unknown key(s): {', '.join(unknown_keys)}"
        )

    validated: dict[str, Any] = {}
    for field_name, field_spec in spec.fields.items():
        scoped_name = f"traps.{trap_id}.{field_name}"
        if field_name in raw:
            validated[field_name] = _validate_field_constraints(
                scoped_name,
                raw[field_name],
                field_spec,
            )
            continue

        if field_spec.default is not MISSING_DEFAULT:
            validated[field_name] = _validate_field_constraints(
                scoped_name,
                field_spec.default,
                field_spec,
            )
            continue

        if field_spec.required:
            raise AttackConfigError(f"{scoped_name} is required")

    return validated


def build_initial_config(shared: SharedConfig, registry: Mapping[str, TrapSpec]) -> dict[str, Any]:
    traps_payload: dict[str, dict[str, Any]] = {}
    for trap_id in sorted(registry):
        spec = registry[trap_id]
        trap_payload: dict[str, Any] = {}
        for field_name, field_spec in spec.fields.items():
            if field_spec.default is MISSING_DEFAULT:
                raise AttackConfigError(
                    f"cannot initialize traps.{trap_id}.{field_name}: missing default"
                )
            trap_payload[field_name] = _validate_field_constraints(
                f"traps.{trap_id}.{field_name}",
                field_spec.default,
                field_spec,
            )
        traps_payload[trap_id] = trap_payload

    return {
        "shared": {
            "scenario": shared.scenario,
            "content_type": shared.content_type,
            "attack_intent": shared.attack_intent,
            "seed": shared.seed,
        },
        "traps": traps_payload,
    }


def write_attack_config(path: Path, payload: Mapping[str, Any]) -> None:
    yaml = _yaml_module()
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def load_attack_config(path: Path, registry: Mapping[str, TrapSpec]) -> LoadedAttackConfig:
    yaml = _yaml_module()

    if not path.exists():
        raise AttackConfigError(f"config file was not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise AttackConfigError("config file is empty")
    if not isinstance(raw, dict):
        raise AttackConfigError("config root must be a mapping")

    allowed_top_level = {"shared", "traps"}
    unknown_top_level = sorted(set(raw) - allowed_top_level)
    if unknown_top_level:
        raise AttackConfigError(
            f"config has unknown top-level key(s): {', '.join(unknown_top_level)}"
        )

    shared_raw = raw.get("shared")
    if not isinstance(shared_raw, dict):
        raise AttackConfigError("shared section must be a mapping")
    shared = _validate_shared_config(shared_raw)

    traps_raw = raw.get("traps", {})
    if not isinstance(traps_raw, dict):
        raise AttackConfigError("traps section must be a mapping")

    unknown_traps = sorted(set(traps_raw) - set(registry))
    if unknown_traps:
        raise AttackConfigError(f"traps has unknown trap id(s): {', '.join(unknown_traps)}")

    trap_configs: dict[str, dict[str, Any]] = {}
    for trap_id in sorted(registry):
        trap_section = traps_raw.get(trap_id, {})
        if trap_section is None:
            trap_section = {}
        if not isinstance(trap_section, dict):
            raise AttackConfigError(f"traps.{trap_id} must be a mapping")

        trap_configs[trap_id] = _validate_trap_config(trap_id, trap_section, registry[trap_id])

    return LoadedAttackConfig(shared=shared, trap_configs=trap_configs)
