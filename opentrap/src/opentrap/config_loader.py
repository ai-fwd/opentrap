from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from opentrap.trap_contract import (
    MISSING_DEFAULT,
    SampleBoundary,
    SharedConfig,
    TrapFieldSpec,
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedTrapConfig:
    shared: SharedConfig
    trap_configs: dict[str, dict[str, Any]]
    product_under_test: str


def _yaml_module():
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "PyYAML is required. Install dependencies before running this command."
        ) from exc
    return yaml


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


SAMPLE_EXTENSIONS = {".html", ".htm", ".txt", ".md", ".json", ".xml", ".csv"}


def load_sample_boundaries(samples_dir: Path) -> tuple[SampleBoundary, ...]:
    if not samples_dir.exists():
        return ()
    if not samples_dir.is_dir():
        raise ConfigError(f"samples path '{samples_dir}' must be a directory")

    discovered = sorted(
        (
            sample_path
            for sample_path in samples_dir.rglob("*")
            if sample_path.is_file() and sample_path.suffix.lower() in SAMPLE_EXTENSIONS
        ),
        key=lambda path: path.relative_to(samples_dir).as_posix(),
    )

    samples: list[SampleBoundary] = []
    for sample_path in discovered:
        try:
            content = sample_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            rel = sample_path.relative_to(samples_dir).as_posix()
            raise ConfigError(
                f"sample file '{rel}' is not valid UTF-8 in {samples_dir}"
            ) from exc
        except OSError as exc:
            rel = sample_path.relative_to(samples_dir).as_posix()
            raise ConfigError(
                f"sample file '{rel}' could not be read from {samples_dir}: {exc}"
            ) from exc

        samples.append(
            SampleBoundary(
                path=sample_path.relative_to(samples_dir).as_posix(),
                content=content,
            )
        )

    return tuple(samples)


def _validate_shared_config(raw: Mapping[str, Any]) -> SharedConfig:
    allowed_keys = {"scenario", "content_style", "trap_intent", "seed"}
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise ConfigError(f"shared has unknown key(s): {', '.join(unknown_keys)}")

    values: dict[str, str] = {}
    for field_name in ("scenario", "content_style", "trap_intent"):
        value = raw.get(field_name)
        if not isinstance(value, str):
            raise ConfigError(f"shared.{field_name} must be a string")
        if not value.strip():
            raise ConfigError(f"shared.{field_name} cannot be empty")
        values[field_name] = value

    seed_raw = raw.get("seed")
    if seed_raw is None:
        seed = None
    elif _is_integer(seed_raw):
        seed = int(seed_raw)
    else:
        raise ConfigError("shared.seed must be an integer or null")

    return SharedConfig(
        scenario=values["scenario"],
        content_style=values["content_style"],
        trap_intent=values["trap_intent"],
        seed=seed,
    )


def _validate_product_under_test(raw: object) -> str:
    if raw is None:
        return "default"
    if not isinstance(raw, str):
        raise ConfigError("product_under_test must be a string")
    product_under_test = raw.strip()
    if not product_under_test:
        raise ConfigError("product_under_test cannot be empty")
    if product_under_test in {".", ".."} or "/" in product_under_test or "\\" in product_under_test:
        raise ConfigError("product_under_test must not contain path separators")
    return product_under_test


def _validate_field_constraints(field_name: str, value: Any, spec: TrapFieldSpec) -> Any:
    if spec.type == "string":
        if not isinstance(value, str):
            raise ConfigError(f"{field_name} must be a string")
        if spec.min_length is not None and len(value) < spec.min_length:
            raise ConfigError(f"{field_name} must be at least {spec.min_length} characters")
        validated_value: Any = value
    elif spec.type == "integer":
        if not _is_integer(value):
            raise ConfigError(f"{field_name} must be an integer")
        validated_value = int(value)
    elif spec.type == "number":
        if not _is_number(value):
            raise ConfigError(f"{field_name} must be a number")
        validated_value = float(value)
    elif spec.type == "boolean":
        if not isinstance(value, bool):
            raise ConfigError(f"{field_name} must be a boolean")
        validated_value = value
    else:
        raise ConfigError(f"{field_name} has unsupported type '{spec.type}'")

    if spec.min is not None and _is_number(validated_value) and validated_value < spec.min:
        raise ConfigError(f"{field_name} must be >= {spec.min}")

    if spec.max is not None and _is_number(validated_value) and validated_value > spec.max:
        raise ConfigError(f"{field_name} must be <= {spec.max}")

    if spec.allowed_values is not None and validated_value not in spec.allowed_values:
        allowed = ", ".join(repr(value) for value in spec.allowed_values)
        raise ConfigError(f"{field_name} must be one of: {allowed}")

    return validated_value


def _validate_trap_config(
    trap_id: str,
    raw: Mapping[str, Any],
    fields: Mapping[str, TrapFieldSpec],
) -> dict[str, Any]:
    unknown_keys = sorted(set(raw) - set(fields))
    if unknown_keys:
        raise ConfigError(
            f"traps.{trap_id} has unknown key(s): {', '.join(unknown_keys)}"
        )

    validated: dict[str, Any] = {}
    for field_name, field_spec in fields.items():
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
            raise ConfigError(f"{scoped_name} is required")

    return validated


def build_initial_trap_config(
    shared: SharedConfig,
    trap_fields_registry: Mapping[str, Mapping[str, TrapFieldSpec]],
) -> dict[str, Any]:
    traps_payload: dict[str, dict[str, Any]] = {}
    for trap_id in sorted(trap_fields_registry):
        fields = trap_fields_registry[trap_id]
        trap_payload: dict[str, Any] = {}
        for field_name, field_spec in fields.items():
            if field_spec.default is MISSING_DEFAULT:
                raise ConfigError(
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
            "content_style": shared.content_style,
            "trap_intent": shared.trap_intent,
            "seed": shared.seed,
        },
        "traps": traps_payload,
    }


def write_trap_config(path: Path, payload: Mapping[str, Any]) -> None:
    yaml = _yaml_module()
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def load_trap_config(
    path: Path,
    trap_fields_registry: Mapping[str, Mapping[str, TrapFieldSpec]],
    samples_dir: Path | None = None,
) -> LoadedTrapConfig:
    yaml = _yaml_module()

    if not path.exists():
        raise ConfigError(f"config file was not found at {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise ConfigError("config file is empty")
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")

    allowed_top_level = {"shared", "traps", "product_under_test"}
    unknown_top_level = sorted(set(raw) - allowed_top_level)
    if unknown_top_level:
        raise ConfigError(
            f"config has unknown top-level key(s): {', '.join(unknown_top_level)}"
        )

    shared_raw = raw.get("shared")
    if not isinstance(shared_raw, dict):
        raise ConfigError("shared section must be a mapping")
    shared = _validate_shared_config(shared_raw)

    traps_raw = raw.get("traps", {})
    if not isinstance(traps_raw, dict):
        raise ConfigError("traps section must be a mapping")
    product_under_test = _validate_product_under_test(raw.get("product_under_test"))

    sample_boundaries = load_sample_boundaries(samples_dir or Path(".opentrap/samples"))
    shared = replace(shared, samples=sample_boundaries)

    unknown_traps = sorted(set(traps_raw) - set(trap_fields_registry))
    if unknown_traps:
        raise ConfigError(f"traps has unknown trap id(s): {', '.join(unknown_traps)}")

    trap_configs: dict[str, dict[str, Any]] = {}
    for trap_id in sorted(trap_fields_registry):
        trap_section = traps_raw.get(trap_id, {})
        if trap_section is None:
            trap_section = {}
        if not isinstance(trap_section, dict):
            raise ConfigError(f"traps.{trap_id} must be a mapping")

        trap_configs[trap_id] = _validate_trap_config(
            trap_id,
            trap_section,
            trap_fields_registry[trap_id],
        )

    return LoadedTrapConfig(
        shared=shared,
        trap_configs=trap_configs,
        product_under_test=product_under_test,
    )
