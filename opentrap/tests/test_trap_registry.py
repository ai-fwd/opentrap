# OpenTrap trap registry tests.
# Verifies trap discovery, contract loading, and registry validation errors.
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from opentrap.trap_registry import TrapRegistryError, build_trap_registry


def _write_trap(trap_dir: Path) -> None:
    trap_dir.mkdir(parents=True)
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
        _shared: SharedConfig,
        _trap: Mapping[str, Any],
        output_base: Path,
    ) -> Path:
        return output_base

    def run(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(context)

    def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        return dict(context)
"""
    (trap_dir / "trap.py").write_text(textwrap.dedent(source), encoding="utf-8")


def test_build_trap_registry_discovers_contracts(tmp_path: Path) -> None:
    _write_trap(tmp_path / "perception" / "prompt-injection")
    _write_trap(tmp_path / "reasoning" / "chain-trap")

    registry = build_trap_registry(tmp_path)

    assert sorted(registry) == ["perception/prompt-injection", "reasoning/chain-trap"]


def test_build_trap_registry_fails_when_contract_missing(tmp_path: Path) -> None:
    _write_trap(tmp_path / "perception" / "prompt-injection")
    (tmp_path / "reasoning" / "missing-contract").mkdir(parents=True)

    with pytest.raises(TrapRegistryError, match="missing trap.py"):
        build_trap_registry(tmp_path)


def test_build_trap_registry_fails_when_class_missing(tmp_path: Path) -> None:
    trap_dir = tmp_path / "reasoning" / "chain-trap"
    trap_dir.mkdir(parents=True)
    (trap_dir / "trap.py").write_text(
        textwrap.dedent(
            """
            class NotTrap:
                pass
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrapRegistryError, match="trap.py must define class Trap"):
        build_trap_registry(tmp_path)


def test_build_trap_registry_fails_when_trap_does_not_inherit_spec(tmp_path: Path) -> None:
    trap_dir = tmp_path / "reasoning" / "chain-trap"
    trap_dir.mkdir(parents=True)
    (trap_dir / "trap.py").write_text(
        textwrap.dedent(
            """
            class Trap:
                pass
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrapRegistryError, match="Trap must inherit TrapSpec"):
        build_trap_registry(tmp_path)
