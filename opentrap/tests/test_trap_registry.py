# OpenTrap trap registry tests.
# Verifies trap discovery, contract loading, and registry validation errors.
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from opentrap.trap_registry import TrapRegistryError, build_trap_registry


def _write_contract(trap_dir: Path, trap_id: str) -> None:
    trap_dir.mkdir(parents=True)
    source = f"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from opentrap.trap_contract import SharedConfig, TrapFieldSpec, TrapSpec


def _run(_shared: SharedConfig, _trap: Mapping[str, Any], output_base: Path) -> Path:
    return output_base


def get_trap_spec() -> TrapSpec:
    return TrapSpec(
        trap_id="{trap_id}",
        fields={{"knob": TrapFieldSpec(type="integer", default=1, min=1)}},
        run=_run,
    )
"""
    (trap_dir / "contract.py").write_text(textwrap.dedent(source), encoding="utf-8")


def test_build_trap_registry_discovers_contracts(tmp_path: Path) -> None:
    _write_contract(tmp_path / "perception" / "prompt-injection", "perception/prompt-injection")
    _write_contract(tmp_path / "reasoning" / "chain-trap", "reasoning/chain-trap")

    registry = build_trap_registry(tmp_path)

    assert sorted(registry) == ["perception/prompt-injection", "reasoning/chain-trap"]


def test_build_trap_registry_fails_when_contract_missing(tmp_path: Path) -> None:
    _write_contract(tmp_path / "perception" / "prompt-injection", "perception/prompt-injection")
    (tmp_path / "reasoning" / "missing-contract").mkdir(parents=True)

    with pytest.raises(TrapRegistryError, match="missing contract.py"):
        build_trap_registry(tmp_path)


def test_build_trap_registry_fails_on_trap_id_mismatch(tmp_path: Path) -> None:
    _write_contract(tmp_path / "reasoning" / "chain-trap", "reasoning/not-chain-trap")

    with pytest.raises(TrapRegistryError, match="trap_id mismatch"):
        build_trap_registry(tmp_path)
