"""Reusable evaluation conventions for OpenTrap traps."""

from __future__ import annotations

from opentrap.evaluation.artifacts import (
    EvaluationArtifacts,
    find_trap_entry,
    load_observed_outputs,
    load_run_manifest,
    write_evaluation_artifacts,
)
from opentrap.evaluation.context import EvaluationContext
from opentrap.evaluation.judge import JudgeResult, LLMJudge
from opentrap.evaluation.result import EvaluationResult
from opentrap.evaluation.runner import find_latest_finalized_run_manifest, run_trap_evaluation
from opentrap.evaluation.scorers import RougeLScoreScorer, SentenceTransformerSbertScorer
from opentrap.evaluation.status import EvaluationStatusEmitter

__all__ = [
    "EvaluationArtifacts",
    "EvaluationContext",
    "EvaluationStatusEmitter",
    "JudgeResult",
    "LLMJudge",
    "RougeLScoreScorer",
    "SentenceTransformerSbertScorer",
    "EvaluationResult",
    "find_latest_finalized_run_manifest",
    "find_trap_entry",
    "load_observed_outputs",
    "load_run_manifest",
    "run_trap_evaluation",
    "write_evaluation_artifacts",
]
