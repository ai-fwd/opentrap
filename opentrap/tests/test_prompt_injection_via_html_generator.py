# OpenTrap trap generator tests.
# Verifies prompt_injection_via_html generation behavior and environment loading.
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


def _trap_dir() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "opentrap"
        / "src"
        / "traps"
        / "perception"
        / "prompt_injection_via_html"
    )


def _load_module(filename: str, module_name: str):
    module_path = _trap_dir() / filename
    trap_path = str(_trap_dir())
    if trap_path not in sys.path:
        sys.path.insert(0, trap_path)

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module {module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _StaticHTMLGenerator:
    def generate(
        self,
        *,
        scenario: str,
        content_style: str,
        seed: int | None,
        samples,
    ) -> str:
        del scenario, content_style, seed, samples
        return (
            "<!DOCTYPE html><html><head><title>Sample</title></head>"
            "<body><h1>Title</h1><p>Paragraph</p></body></html>"
        )


class _TrackingHTMLGenerator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int | None, int]] = []

    def generate(
        self,
        *,
        scenario: str,
        content_style: str,
        seed: int | None,
        samples,
    ) -> str:
        self.calls.append((scenario, content_style, seed, len(samples)))
        return (
            "<!DOCTYPE html><html><head><title>Sample</title></head>"
            "<body><h1>Title</h1><p>Paragraph</p></body></html>"
        )


def _load_records(run_dir: Path) -> list[dict]:
    metadata_path = run_dir / "metadata.jsonl"
    return [
        json.loads(line)
        for line in metadata_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_generator_is_deterministic_for_same_seed(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize hotel reviews",
        content_style="reviews",
        trap_intent="turn all bad reviews into positive reviews",
        location_temperature=0.25,
        density_temperature=0.5,
        diversity_temperature=0.5,
        seed=12345,
        base_count=2,
        run_id="fixed-run",
    )

    run_one = generator_module.run_generation(
        config=config,
        output_base=tmp_path / "out1",
        base_html_generator=_StaticHTMLGenerator(),
    )
    run_two = generator_module.run_generation(
        config=config,
        output_base=tmp_path / "out2",
        base_html_generator=_StaticHTMLGenerator(),
    )

    records_one = _load_records(run_one)
    records_two = _load_records(run_two)
    assert records_one == records_two

    for record in records_one:
        filename = record["filename"]
        content_one = (run_one / "data" / filename).read_text(encoding="utf-8")
        content_two = (run_two / "data" / filename).read_text(encoding="utf-8")
        assert content_one == content_two


def test_file_count_invariants_hold(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize blogs",
        content_style="blogs",
        trap_intent="insert fabricated summary claims",
        seed=9,
        base_count=3,
        run_id="count-check",
    )
    run_dir = generator_module.run_generation(
        config=config,
        output_base=tmp_path,
        base_html_generator=_StaticHTMLGenerator(),
    )
    records = _load_records(run_dir)

    assert len(records) == 27
    assert len([r for r in records if not r["is_poisoned"]]) == 3
    assert len([r for r in records if r["is_poisoned"]]) == 24
    assert len(list((run_dir / "data").glob("*.htm"))) == 27


def test_temperature_mapping_math() -> None:
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    assert generator_module.compute_insertion_count(0.0) == 1
    assert generator_module.compute_insertion_count(0.5) == 4
    assert generator_module.compute_insertion_count(1.0) == 8

    assert generator_module.compute_distinct_count(0.0, 8) == 1
    assert generator_module.compute_distinct_count(0.5, 8) == 4
    assert generator_module.compute_distinct_count(1.0, 8) == 8
    assert generator_module.compute_distinct_count(1.0, 1) == 1


def test_density_ceiling_wins_for_diversity_one_density_zero(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize docs",
        content_style="docs",
        trap_intent="override to biased response",
        density_temperature=0.0,
        diversity_temperature=1.0,
        seed=42,
        base_count=1,
        run_id="density-ceiling",
    )
    run_dir = generator_module.run_generation(
        config=config,
        output_base=tmp_path,
        base_html_generator=_StaticHTMLGenerator(),
    )
    records = _load_records(run_dir)

    poisoned = [record for record in records if record["is_poisoned"]]
    assert len(poisoned) == 8
    assert all(record["injection_count"] == 1 for record in poisoned)
    assert all(len(record["attack_types"]) == 1 for record in poisoned)


def test_metadata_links_base_and_poisoned_records(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize product pages",
        content_style="docs",
        trap_intent="force favorable framing",
        density_temperature=0.0,
        diversity_temperature=0.0,
        seed=77,
        base_count=1,
        run_id="metadata-check",
    )
    run_dir = generator_module.run_generation(
        config=config,
        output_base=tmp_path,
        base_html_generator=_StaticHTMLGenerator(),
    )
    records = _load_records(run_dir)

    base_records = [record for record in records if not record["is_poisoned"]]
    poisoned_records = [record for record in records if record["is_poisoned"]]

    assert len(base_records) == 1
    assert len(poisoned_records) == 8

    base_id = base_records[0]["file_id"]
    assert base_records[0]["attack_types"] == []
    assert base_records[0]["injection_count"] == 0

    poison_attack_heads = [record["attack_types"][0] for record in poisoned_records]
    assert set(poison_attack_heads) == set(generator_module.ATTACK_TYPES)

    for record in poisoned_records:
        assert record["base_file_id"] == base_id
        assert 0 <= record["variant_index"] <= 7
        assert record["injection_count"] == len(record["attack_types"])
        assert len(record["injection_locations"]) == record["injection_count"]


def test_location_randomization_stays_within_valid_families(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize reviews",
        content_style="reviews",
        trap_intent="rewrite negatives as positives",
        location_temperature=1.0,
        density_temperature=1.0,
        diversity_temperature=1.0,
        seed=2026,
        base_count=1,
        run_id="location-check",
    )
    run_dir = generator_module.run_generation(
        config=config,
        output_base=tmp_path,
        base_html_generator=_StaticHTMLGenerator(),
    )
    records = _load_records(run_dir)
    poisoned_records = [record for record in records if record["is_poisoned"]]

    observed_locations = {
        location for record in poisoned_records for location in record["injection_locations"]
    }
    assert observed_locations.issubset(set(generator_module.VALID_LOCATIONS))
    assert observed_locations


def test_run_generation_uses_injected_generator(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize changelog",
        content_style="release-notes",
        trap_intent="insert fabricated achievements",
        seed=100,
        base_count=2,
        run_id="injected-generator",
    )
    tracking_generator = _TrackingHTMLGenerator()
    generator_module.run_generation(
        config=config,
        output_base=tmp_path,
        base_html_generator=tracking_generator,
    )

    assert tracking_generator.calls == [
        ("summarize changelog", "release-notes", 100, 0),
        ("summarize changelog", "release-notes", 101, 0),
    ]


def test_trap_constructor_does_not_load_llm_config(monkeypatch) -> None:
    trap_module = _load_module("trap.py", "prompt_injection_via_html_trap_ctor")
    calls = {"load_count": 0}

    def _fake_load_llm_config_from_env():
        calls["load_count"] += 1
        return object()

    monkeypatch.setattr(trap_module, "load_llm_config_from_env", _fake_load_llm_config_from_env)

    trap_module.Trap()
    assert calls["load_count"] == 0


def test_trap_generate_uses_injected_dataset_generator(monkeypatch, tmp_path: Path) -> None:
    trap_module = _load_module("trap.py", "prompt_injection_via_html_trap_generate_injected")
    from opentrap.trap_contract import SharedConfig

    calls = {"load_count": 0, "dataset_generate_count": 0}

    def _fake_load_llm_config_from_env():
        calls["load_count"] += 1
        return object()

    class _FakeDatasetGenerator:
        def generate(self, config, output_base: Path) -> Path:  # noqa: ANN001
            del config
            calls["dataset_generate_count"] += 1
            output_base.mkdir(parents=True, exist_ok=True)
            artifact = output_base / "artifact.txt"
            artifact.write_text("generated", encoding="utf-8")
            return artifact

    monkeypatch.setattr(trap_module, "load_llm_config_from_env", _fake_load_llm_config_from_env)

    trap = trap_module.Trap(dataset_generator=_FakeDatasetGenerator())
    shared = SharedConfig(
        scenario="summarize docs",
        content_style="docs",
        trap_intent="rewrite negatives",
        seed=123,
    )
    trap_config = {
        "location_temperature": 0.0,
        "density_temperature": 0.0,
        "diversity_temperature": 0.0,
        "base_count": 1,
    }

    run_one = trap.generate(shared, trap_config, tmp_path / "run-one")
    run_two = trap.generate(shared, trap_config, tmp_path / "run-two")

    assert run_one.exists()
    assert run_two.exists()
    assert calls["load_count"] == 0
    assert calls["dataset_generate_count"] == 2


def test_trap_generate_initializes_default_generator_once(monkeypatch, tmp_path: Path) -> None:
    trap_module = _load_module("trap.py", "prompt_injection_via_html_trap_generate_default")
    from opentrap.trap_contract import SharedConfig

    calls = {"load_count": 0, "llm_generator_init_count": 0}

    def _fake_load_llm_config_from_env():
        calls["load_count"] += 1
        return object()

    class _FakeLLMHTMLGenerator:
        def __init__(self, _cfg: object) -> None:
            calls["llm_generator_init_count"] += 1

        def generate(
            self,
            *,
            scenario: str,
            content_style: str,
            seed: int | None,
            samples,
        ) -> str:
            del scenario, content_style, seed, samples
            return (
                "<!DOCTYPE html><html><head><title>Sample</title></head>"
                "<body><h1>Title</h1><p>Paragraph</p></body></html>"
            )

    monkeypatch.setattr(trap_module, "load_llm_config_from_env", _fake_load_llm_config_from_env)
    monkeypatch.setattr(trap_module, "LLMHTMLGenerator", _FakeLLMHTMLGenerator)

    trap = trap_module.Trap()
    shared = SharedConfig(
        scenario="summarize docs",
        content_style="docs",
        trap_intent="rewrite negatives",
        seed=123,
    )
    trap_config = {
        "location_temperature": 0.0,
        "density_temperature": 0.0,
        "diversity_temperature": 0.0,
        "base_count": 1,
    }

    run_one = trap.generate(shared, trap_config, tmp_path / "run-one")
    run_two = trap.generate(shared, trap_config, tmp_path / "run-two")

    assert run_one.exists()
    assert run_two.exists()
    assert calls["load_count"] == 1
    assert calls["llm_generator_init_count"] == 1


def test_bootstrap_openai_url_normalization_and_defaults(monkeypatch) -> None:
    bootstrap_module = _load_module("bootstrap.py", "prompt_injection_via_html_bootstrap")
    monkeypatch.setattr(bootstrap_module, "load_layered_env", lambda: None)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")

    monkeypatch.setenv("OPENAI_URL", "https://api.openai.com")
    assert bootstrap_module.load_llm_config_from_env().base_url == "https://api.openai.com/v1"

    monkeypatch.setenv("OPENAI_URL", "https://api.openai.com/v1")
    assert bootstrap_module.load_llm_config_from_env().base_url == "https://api.openai.com/v1"

    monkeypatch.setenv("OPENAI_URL", "https://api.openai.com/v1/responses")
    assert bootstrap_module.load_llm_config_from_env().base_url == "https://api.openai.com/v1"

    monkeypatch.delenv("OPENAI_URL", raising=False)
    assert bootstrap_module.load_llm_config_from_env().base_url == "https://api.openai.com/v1"


def test_bootstrap_load_layered_env_reads_opentrap_env_file(monkeypatch) -> None:
    bootstrap_module = _load_module("bootstrap.py", "prompt_injection_via_html_bootstrap")

    seen_paths: list[Path] = []

    def _fake_dotenv_values(path: Path) -> dict[str, str]:
        seen_paths.append(path)
        if path.name == ".env":
            return {"OPENAI_API_KEY": "local-key", "OPENAI_MODEL": "local-model"}
        return {}

    monkeypatch.setitem(
        sys.modules,
        "dotenv",
        type("_DotenvModule", (), {"dotenv_values": _fake_dotenv_values}),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    bootstrap_module.load_layered_env()

    expected_root = Path(__file__).resolve().parents[2]
    assert seen_paths == [expected_root / "opentrap" / ".env"]
    assert "OPENAI_API_KEY" in os.environ
    assert os.environ["OPENAI_API_KEY"] == "local-key"
    assert os.environ["OPENAI_MODEL"] == "local-model"


def test_file_naming_is_sequential_and_deterministic(tmp_path: Path) -> None:
    config_module = _load_module("config.py", "prompt_injection_via_html_config")
    generator_module = _load_module("generate.py", "prompt_injection_via_html_generate")

    config = config_module.GenerationConfig(
        scenario="summarize docs",
        content_style="docs",
        trap_intent="override style guide",
        seed=11,
        base_count=1,
        run_id="filename-check",
    )
    run_dir = generator_module.run_generation(
        config=config,
        output_base=tmp_path,
        base_html_generator=_StaticHTMLGenerator(),
    )
    records = _load_records(run_dir)
    filenames = [record["filename"] for record in records]
    assert filenames == [f"{i:05d}.htm" for i in range(1, 10)]


def test_bootstrap_requires_openai_env(monkeypatch) -> None:
    bootstrap_module = _load_module("bootstrap.py", "prompt_injection_via_html_bootstrap")

    monkeypatch.setattr(bootstrap_module, "load_layered_env", lambda: None)
    for name in ("OPENAI_API_KEY", "OPENAI_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)

    try:
        bootstrap_module.load_llm_config_from_env()
        raise AssertionError("Expected RuntimeError for missing environment")
    except RuntimeError as exc:
        message = str(exc)
        assert "OPENAI_API_KEY" in message
        assert "OPENAI_MODEL" in message
