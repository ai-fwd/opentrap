from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from opentrap.config_loader import AttackConfigError, load_attack_config
from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


def _noop_run(_shared: SharedConfig, _trap: Mapping[str, Any], output_base: Path) -> Path:
    return output_base


def _registry() -> dict[str, TrapSpec]:
    return {
        "reasoning/chain-trap": TrapSpec(
            trap_id="reasoning/chain-trap",
            fields={
                "temperature": TrapFieldSpec(type="number", default=0.0, min=0.0, max=1.0),
                "base_count": TrapFieldSpec(type="integer", default=3, min=1),
            },
            run=_noop_run,
        )
    }


def test_load_attack_config_applies_trap_defaults(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": None,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    samples_dir = tmp_path / ".opentrap" / "samples"

    loaded = load_attack_config(config_path, _registry(), samples_dir=samples_dir)

    assert loaded.shared.seed is None
    assert loaded.shared.samples == ()
    assert loaded.trap_configs["reasoning/chain-trap"] == {
        "temperature": 0.0,
        "base_count": 3,
    }


def test_load_attack_config_rejects_unknown_trap_id(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": 10,
        },
        "traps": {
            "reasoning/chain-trap": {},
            "memory/unknown": {},
        },
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(AttackConfigError, match="unknown trap id"):
        load_attack_config(config_path, _registry(), samples_dir=tmp_path / ".opentrap/samples")


def test_load_attack_config_rejects_unknown_trap_field(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": 10,
        },
        "traps": {"reasoning/chain-trap": {"unexpected": 1}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(AttackConfigError, match="unknown key"):
        load_attack_config(config_path, _registry(), samples_dir=tmp_path / ".opentrap/samples")


def test_load_attack_config_rejects_invalid_seed(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": "not-int",
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(AttackConfigError, match="shared.seed"):
        load_attack_config(config_path, _registry(), samples_dir=tmp_path / ".opentrap/samples")


def test_load_attack_config_enforces_numeric_range(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {"temperature": 2.0}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(AttackConfigError, match="<= 1.0"):
        load_attack_config(config_path, _registry(), samples_dir=tmp_path / ".opentrap/samples")


def test_load_attack_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {}},
        "extra": {},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(AttackConfigError, match="unknown top-level"):
        load_attack_config(config_path, _registry(), samples_dir=tmp_path / ".opentrap/samples")


def test_load_attack_config_loads_samples_recursively(tmp_path: Path) -> None:
    samples_dir = tmp_path / ".opentrap" / "samples"
    (samples_dir / "nested").mkdir(parents=True)
    (samples_dir / "b.html").write_text("<html>b</html>", encoding="utf-8")
    (samples_dir / "nested" / "a.md").write_text("# sample", encoding="utf-8")
    (samples_dir / "nested" / "ignore.bin").write_bytes(b"\x00\x01")

    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_attack_config(config_path, _registry(), samples_dir=samples_dir)

    assert [sample.path for sample in loaded.shared.samples] == ["b.html", "nested/a.md"]
    assert loaded.shared.samples[0].content == "<html>b</html>"
    assert loaded.shared.samples[1].content == "# sample"


def test_load_attack_config_rejects_non_utf8_sample(tmp_path: Path) -> None:
    samples_dir = tmp_path / ".opentrap" / "samples"
    samples_dir.mkdir(parents=True)
    (samples_dir / "bad.html").write_bytes(b"\xff\xfe")

    payload = {
        "shared": {
            "scenario": "summarize docs",
            "content_style": "docs",
            "attack_intent": "rewrite negatives",
            "seed": 123,
        },
        "traps": {"reasoning/chain-trap": {}},
    }
    config_path = tmp_path / "opentrap.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(AttackConfigError, match="not valid UTF-8"):
        load_attack_config(config_path, _registry(), samples_dir=samples_dir)
