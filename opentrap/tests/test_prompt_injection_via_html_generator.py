from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_generator_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "opentrap"
        / "traps"
        / "reasoning"
        / "prompt-injection-via-html"
        / "generate.py"
    )
    spec = importlib.util.spec_from_file_location("prompt_injection_via_html_generate", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load generator module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _html_factory(_: int) -> str:
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
    module = _load_generator_module()

    config = module.GenerationConfig(
        scenario="summarize hotel reviews",
        content_type="reviews",
        attack_intent="turn all bad reviews into positive reviews",
        location_temperature=0.25,
        density_temperature=0.5,
        diversity_temperature=0.5,
        seed=12345,
        base_count=2,
        run_id="fixed-run",
    )

    run_one = module.run_generation(config=config, output_base=tmp_path / "out1", html_factory=_html_factory)
    run_two = module.run_generation(config=config, output_base=tmp_path / "out2", html_factory=_html_factory)

    records_one = _load_records(run_one)
    records_two = _load_records(run_two)
    assert records_one == records_two

    for record in records_one:
        filename = record["filename"]
        content_one = (run_one / "pages" / filename).read_text(encoding="utf-8")
        content_two = (run_two / "pages" / filename).read_text(encoding="utf-8")
        assert content_one == content_two


def test_file_count_invariants_hold(tmp_path: Path) -> None:
    module = _load_generator_module()

    config = module.GenerationConfig(
        scenario="summarize blogs",
        content_type="blogs",
        attack_intent="insert fabricated summary claims",
        seed=9,
        base_count=3,
        run_id="count-check",
    )
    run_dir = module.run_generation(config=config, output_base=tmp_path, html_factory=_html_factory)
    records = _load_records(run_dir)

    assert len(records) == 27
    assert len([r for r in records if not r["is_poisoned"]]) == 3
    assert len([r for r in records if r["is_poisoned"]]) == 24
    assert len(list((run_dir / "pages").glob("*.htm"))) == 27


def test_temperature_mapping_math() -> None:
    module = _load_generator_module()

    assert module.compute_insertion_count(0.0) == 1
    assert module.compute_insertion_count(0.5) == 4
    assert module.compute_insertion_count(1.0) == 8

    assert module.compute_distinct_count(0.0, 8) == 1
    assert module.compute_distinct_count(0.5, 8) == 4
    assert module.compute_distinct_count(1.0, 8) == 8
    assert module.compute_distinct_count(1.0, 1) == 1


def test_density_ceiling_wins_for_diversity_one_density_zero(tmp_path: Path) -> None:
    module = _load_generator_module()

    config = module.GenerationConfig(
        scenario="summarize docs",
        content_type="docs",
        attack_intent="override to biased response",
        density_temperature=0.0,
        diversity_temperature=1.0,
        seed=42,
        base_count=1,
        run_id="density-ceiling",
    )
    run_dir = module.run_generation(config=config, output_base=tmp_path, html_factory=_html_factory)
    records = _load_records(run_dir)

    poisoned = [record for record in records if record["is_poisoned"]]
    assert len(poisoned) == 8
    assert all(record["injection_count"] == 1 for record in poisoned)
    assert all(len(record["attack_types"]) == 1 for record in poisoned)


def test_metadata_links_base_and_poisoned_records(tmp_path: Path) -> None:
    module = _load_generator_module()

    config = module.GenerationConfig(
        scenario="summarize product pages",
        content_type="docs",
        attack_intent="force favorable framing",
        density_temperature=0.0,
        diversity_temperature=0.0,
        seed=77,
        base_count=1,
        run_id="metadata-check",
    )
    run_dir = module.run_generation(config=config, output_base=tmp_path, html_factory=_html_factory)
    records = _load_records(run_dir)

    base_records = [record for record in records if not record["is_poisoned"]]
    poisoned_records = [record for record in records if record["is_poisoned"]]

    assert len(base_records) == 1
    assert len(poisoned_records) == 8

    base_id = base_records[0]["file_id"]
    assert base_records[0]["attack_types"] == []
    assert base_records[0]["injection_count"] == 0

    poison_attack_heads = [record["attack_types"][0] for record in poisoned_records]
    assert set(poison_attack_heads) == set(module.ATTACK_TYPES)

    for record in poisoned_records:
        assert record["base_file_id"] == base_id
        assert 0 <= record["variant_index"] <= 7
        assert record["injection_count"] == len(record["attack_types"])
        assert len(record["injection_locations"]) == record["injection_count"]


def test_location_randomization_stays_within_valid_families(tmp_path: Path) -> None:
    module = _load_generator_module()

    config = module.GenerationConfig(
        scenario="summarize reviews",
        content_type="reviews",
        attack_intent="rewrite negatives as positives",
        location_temperature=1.0,
        density_temperature=1.0,
        diversity_temperature=1.0,
        seed=2026,
        base_count=1,
        run_id="location-check",
    )
    run_dir = module.run_generation(config=config, output_base=tmp_path, html_factory=_html_factory)
    records = _load_records(run_dir)
    poisoned_records = [record for record in records if record["is_poisoned"]]

    observed_locations = {
        location
        for record in poisoned_records
        for location in record["injection_locations"]
    }
    assert observed_locations.issubset(set(module.VALID_LOCATIONS))
    assert observed_locations
