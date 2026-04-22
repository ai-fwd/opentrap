# OpenTrap config loader tests.
# Verifies schema validation, defaults, sample loading, and product_under_test handling.
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from opentrap.config_loader import ConfigError, load_trap_config
from opentrap.trap_contract import TrapFieldSpec


def _trap_fields_registry() -> dict[str, dict[str, TrapFieldSpec]]:
    return {
        "reasoning/chain-trap": {
            "temperature": TrapFieldSpec(type="number", default=0.0, min=0.0, max=1.0),
            "base_count": TrapFieldSpec(type="integer", default=3, min=1),
        }
    }


def _base_payload() -> dict:
    return {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": None,
        },
        "harness": {
            "command": ["bunx", "playwright", "test"],
            "cwd": "acme-client",
        },
        "traps": {"reasoning/chain-trap": {}},
    }


def test_load_trap_config_applies_trap_defaults(tmp_path: Path) -> None:
    payload = _base_payload()
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"

    loaded = load_trap_config(config_path, _trap_fields_registry(), samples_dir=samples_dir)

    assert loaded.shared.seed is None
    assert loaded.shared.samples == ()
    assert loaded.product_under_test == "default"
    assert loaded.harness.command == ("bunx", "playwright", "test")
    assert loaded.harness.cwd == "acme-client"
    assert loaded.trap_configs["reasoning/chain-trap"] == {
        "temperature": 0.0,
        "base_count": 3,
    }


def test_load_trap_config_reads_optional_product_under_test(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["product_under_test"] = "acme-client"
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_trap_config(
        config_path,
        _trap_fields_registry(),
        samples_dir=tmp_path / ".opentrap/samples",
    )

    assert loaded.product_under_test == "acme-client"


def test_load_trap_config_rejects_invalid_product_under_test(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["product_under_test"] = "nested/path"
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="product_under_test"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_missing_required_harness(tmp_path: Path) -> None:
    payload = _base_payload()
    payload.pop("harness")
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="harness section must be a mapping"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


@pytest.mark.parametrize(
    ("command", "error_match"),
    [
        (None, "harness.command must be a non-empty list"),
        ([], "harness.command must be a non-empty list"),
        ([""], "harness.command\\[0\\] must be a non-empty string"),
        (["bunx", 1], "harness.command\\[1\\] must be a non-empty string"),
    ],
)
def test_load_trap_config_rejects_invalid_harness_command(
    tmp_path: Path,
    command: object,
    error_match: str,
) -> None:
    payload = _base_payload()
    payload["harness"]["command"] = command
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match=error_match):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


@pytest.mark.parametrize(
    ("cwd", "error_match"),
    [
        (None, "harness.cwd must be a string"),
        ("", "harness.cwd cannot be empty"),
        ("   ", "harness.cwd cannot be empty"),
        ("/abs/path", "harness.cwd must be relative"),
    ],
)
def test_load_trap_config_rejects_invalid_harness_cwd(
    tmp_path: Path,
    cwd: object,
    error_match: str,
) -> None:
    payload = _base_payload()
    payload["harness"]["cwd"] = cwd
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match=error_match):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_unknown_trap_id(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["shared"]["seed"] = 10
    payload["traps"]["memory/unknown"] = {}
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown trap id"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_unknown_trap_field(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["shared"]["seed"] = 10
    payload["traps"]["reasoning/chain-trap"] = {"unexpected": 1}
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown key"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_invalid_seed(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["shared"]["seed"] = "not-int"
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="shared.seed"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_enforces_numeric_range(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["shared"]["seed"] = 123
    payload["traps"]["reasoning/chain-trap"] = {"temperature": 2.0}
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="<= 1.0"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    payload = _base_payload()
    payload["shared"]["seed"] = 123
    payload["extra"] = {}
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown top-level"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_loads_samples_recursively(tmp_path: Path) -> None:
    samples_dir = tmp_path / ".opentrap" / "samples"
    (samples_dir / "nested").mkdir(parents=True)
    (samples_dir / "b.html").write_text("<html>b</html>", encoding="utf-8")
    (samples_dir / "nested" / "a.md").write_text("# sample", encoding="utf-8")
    (samples_dir / "nested" / "ignore.bin").write_bytes(b"\x00\x01")

    payload = _base_payload()
    payload["shared"]["seed"] = 123
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_trap_config(config_path, _trap_fields_registry(), samples_dir=samples_dir)

    assert [sample.path for sample in loaded.shared.samples] == ["b.html", "nested/a.md"]
    assert loaded.shared.samples[0].content == "<html>b</html>"
    assert loaded.shared.samples[1].content == "# sample"


def test_load_trap_config_rejects_non_utf8_sample(tmp_path: Path) -> None:
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)
    (samples_dir / "bad.html").write_bytes(b"\xff\xfe")

    payload = _base_payload()
    payload["shared"]["seed"] = 123
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_trap_config(config_path, _trap_fields_registry(), samples_dir=samples_dir)
