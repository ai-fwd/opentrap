from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from opentrap.trap_registry import TrapRegistryError
from opentrap.trap_registry_loader import load_registry_from_candidates


def _write_valid_trap(root: Path, trap_id: str) -> None:
    target, name = trap_id.split("/", 1)
    trap_dir = root / target / name
    trap_dir.mkdir(parents=True, exist_ok=True)
    source = """
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


class Trap(TrapSpec[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]):
    trap_id = ""
    fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

    def generate(
        self,
        shared_config: SharedConfig,
        trap_config: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        del shared_config, trap_config
        return output_base

    def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(context)

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(context)
"""
    (trap_dir / "trap.py").write_text(textwrap.dedent(source), encoding="utf-8")


def test_load_registry_from_candidates_uses_first_valid_directory(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_valid_trap(first, "perception/prompt-injection")
    _write_valid_trap(second, "reasoning/chain-trap")

    registry = load_registry_from_candidates((first, second))

    assert registry is not None
    assert registry.trap_ids == ("perception/prompt-injection",)


def test_load_registry_from_candidates_skips_missing_directory_and_uses_next(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    second = tmp_path / "second"
    _write_valid_trap(second, "reasoning/chain-trap")

    registry = load_registry_from_candidates((missing, second))

    assert registry is not None
    assert registry.trap_ids == ("reasoning/chain-trap",)


def test_load_registry_from_candidates_raises_when_existing_candidates_fail(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "broken"
    (broken / "reasoning" / "missing-contract").mkdir(parents=True)
    missing = tmp_path / "missing"

    with pytest.raises(TrapRegistryError, match="missing trap.py"):
        load_registry_from_candidates((broken, missing))


def test_load_registry_from_candidates_returns_none_when_all_missing(
    tmp_path: Path,
) -> None:
    first = tmp_path / "missing-a"
    second = tmp_path / "missing-b"

    registry = load_registry_from_candidates((first, second))

    assert registry is None
