# OpenTrap trap registry tests.
# Verifies lazy discovery, class/field loading, and trap instantiation behavior.
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from opentrap.trap.registry import TrapRegistryError, build_trap_registry


def _write_trap(trap_dir: Path, source: str) -> None:
    trap_dir.mkdir(parents=True)
    (trap_dir / "trap.py").write_text(textwrap.dedent(source), encoding="utf-8")


def test_build_trap_registry_discovers_trap_ids_without_instantiation(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "perception" / "prompt-injection",
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Any, Mapping
        from opentrap.trap import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

        class Trap(TrapSpec):
            trap_id = ""
            fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

            def __init__(self) -> None:
                raise RuntimeError("constructor should not run during discovery")

            def generate(
                self,
                _shared: SharedConfig,
                _trap: Mapping[str, Any],
                output_base: Path,
            ) -> Path:
                return output_base

            def build_cases(self, _context: TrapCaseContext) -> list[dict[str, Any]]:
                return []

            def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)

            def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)
        """,
    )
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Any, Mapping
        from opentrap.trap import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

        class Trap(TrapSpec):
            trap_id = ""
            fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

            def generate(
                self,
                _shared: SharedConfig,
                _trap: Mapping[str, Any],
                output_base: Path,
            ) -> Path:
                return output_base

            def build_cases(self, _context: TrapCaseContext) -> list[dict[str, Any]]:
                return []

            def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)

            def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)
        """,
    )

    registry = build_trap_registry(tmp_path)

    assert registry.trap_ids == ("perception/prompt-injection", "reasoning/chain-trap")


def test_build_trap_registry_fails_when_contract_missing(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "perception" / "prompt-injection",
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Any, Mapping
        from opentrap.trap import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

        class Trap(TrapSpec):
            trap_id = ""
            fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

            def generate(
                self,
                _shared: SharedConfig,
                _trap: Mapping[str, Any],
                output_base: Path,
            ) -> Path:
                return output_base

            def build_cases(self, _context: TrapCaseContext) -> list[dict[str, Any]]:
                return []

            def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)

            def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)
        """,
    )
    (tmp_path / "reasoning" / "missing-contract").mkdir(parents=True)

    with pytest.raises(TrapRegistryError, match="missing trap.py"):
        build_trap_registry(tmp_path)


def test_build_trap_registry_does_not_import_modules(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        raise RuntimeError("module imported unexpectedly")
        """,
    )

    registry = build_trap_registry(tmp_path)
    assert registry.trap_ids == ("reasoning/chain-trap",)


def test_load_trap_class_fails_when_class_missing(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        class NotTrap:
            pass
        """,
    )

    registry = build_trap_registry(tmp_path)

    with pytest.raises(TrapRegistryError, match="trap.py must define class Trap"):
        registry.load_trap_class("reasoning/chain-trap")


def test_load_trap_class_fails_when_trap_does_not_inherit_spec(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        class Trap:
            pass
        """,
    )

    registry = build_trap_registry(tmp_path)

    with pytest.raises(TrapRegistryError, match="Trap must inherit TrapSpec"):
        registry.load_trap_class("reasoning/chain-trap")


def test_load_trap_fields_does_not_instantiate_trap(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Any, Mapping
        from opentrap.trap import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

        class Trap(TrapSpec):
            trap_id = ""
            fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

            def __init__(self) -> None:
                raise RuntimeError("constructor should not run when loading fields")

            def generate(
                self,
                _shared: SharedConfig,
                _trap: Mapping[str, Any],
                output_base: Path,
            ) -> Path:
                return output_base

            def build_cases(self, _context: TrapCaseContext) -> list[dict[str, Any]]:
                return []

            def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)

            def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)
        """,
    )

    registry = build_trap_registry(tmp_path)
    fields = registry.load_trap_fields("reasoning/chain-trap")

    assert "knob" in fields


def test_create_trap_instantiates_and_assigns_trap_id(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Any, Mapping
        from opentrap.trap import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

        class Trap(TrapSpec):
            trap_id = ""
            fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

            def __init__(self) -> None:
                self.created = True

            def generate(
                self,
                _shared: SharedConfig,
                _trap: Mapping[str, Any],
                output_base: Path,
            ) -> Path:
                return output_base

            def build_cases(self, _context: TrapCaseContext) -> list[dict[str, Any]]:
                return []

            def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)

            def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)
        """,
    )

    registry = build_trap_registry(tmp_path)
    trap = registry.create_trap("reasoning/chain-trap")

    assert trap.trap_id == "reasoning/chain-trap"


def test_create_trap_surfaces_constructor_failure(tmp_path: Path) -> None:
    _write_trap(
        tmp_path / "reasoning" / "chain-trap",
        """
        from __future__ import annotations
        from pathlib import Path
        from typing import Any, Mapping
        from opentrap.trap import SharedConfig, TrapCaseContext, TrapFieldSpec, TrapSpec

        class Trap(TrapSpec):
            trap_id = ""
            fields = {"knob": TrapFieldSpec(type="integer", default=1, min=1)}

            def __init__(self) -> None:
                raise RuntimeError("ctor boom")

            def generate(
                self,
                _shared: SharedConfig,
                _trap: Mapping[str, Any],
                output_base: Path,
            ) -> Path:
                return output_base

            def build_cases(self, _context: TrapCaseContext) -> list[dict[str, Any]]:
                return []

            def bind(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)

            def evaluate(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
                return dict(context)
        """,
    )

    registry = build_trap_registry(tmp_path)
    with pytest.raises(TrapRegistryError, match="Trap\\(\\) failed"):
        registry.create_trap("reasoning/chain-trap")
