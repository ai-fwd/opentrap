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


def test_load_trap_config_applies_trap_defaults(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"

    loaded = load_trap_config(config_path, _trap_fields_registry(), samples_dir=samples_dir)

    assert loaded.shared.seed is None
    assert loaded.shared.samples == ()
    assert loaded.product_under_test == "default"
    assert loaded.trap_configs["reasoning/chain-trap"] == {
        "temperature": 0.0,
        "base_count": 3,
    }


def test_load_trap_config_reads_optional_product_under_test(tmp_path: Path) -> None:
    payload = {
        "product_under_test": "acme-client",
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_trap_config(
        config_path,
        _trap_fields_registry(),
        samples_dir=tmp_path / ".opentrap/samples",
    )

    assert loaded.product_under_test == "acme-client"


def test_load_trap_config_rejects_invalid_product_under_test(tmp_path: Path) -> None:
    payload = {
        "product_under_test": "nested/path",
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="product_under_test"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_unknown_trap_id(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": 10,
        },
        "traps": {
            "reasoning/chain-trap": {},
            "memory/unknown": {},
        },
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown trap id"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_unknown_trap_field(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": 10,
        },
        "traps": {"reasoning/chain-trap": {"unexpected": 1}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown key"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_invalid_seed(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": "not-int",
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="shared.seed"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_enforces_numeric_range(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {"temperature": 2.0}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="<= 1.0"):
        load_trap_config(
            config_path,
            _trap_fields_registry(),
            samples_dir=tmp_path / ".opentrap/samples",
        )


def test_load_trap_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {}},
        "extra": {},
    }
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

    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
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

    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "trap_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_trap_config(config_path, _trap_fields_registry(), samples_dir=samples_dir)
