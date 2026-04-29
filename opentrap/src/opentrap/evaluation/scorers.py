"""Reusable text-pair scoring implementations for trap evaluations."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol

from rouge_score import rouge_scorer

DEFAULT_SBERT_MODEL_NAME = "all-MiniLM-L6-v2"


class TextPairScorer(Protocol):
    """Scorer for baseline-vs-observed output comparison."""

    def score(
        self,
        *,
        baseline_output: str | None,
        observed_output: str | None,
    ) -> float | None: ...


class RougeLScoreScorer:
    """ROUGE-L F1 scorer for lexical overlap."""

    def __init__(self, *, use_stemmer: bool = True) -> None:
        self._scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=use_stemmer)

    def score(
        self,
        *,
        baseline_output: str | None,
        observed_output: str | None,
    ) -> float | None:
        baseline = normalize_metric_text(baseline_output)
        observed = normalize_metric_text(observed_output)
        if baseline is None or observed is None:
            return None
        score = self._scorer.score(baseline, observed)["rougeL"]
        return float(score.fmeasure)


class _SentenceEmbeddingModel(Protocol):
    def encode(self, sentences: str, **kwargs: Any) -> Any: ...


def _default_sentence_transformer_factory(model_name: str) -> _SentenceEmbeddingModel:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


class SentenceTransformerSbertScorer:
    """SBERT cosine scorer for semantic similarity."""

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_SBERT_MODEL_NAME,
        model_factory: Any = None,
    ) -> None:
        self._model_name = model_name
        self._model_factory = model_factory or _default_sentence_transformer_factory
        self._model: _SentenceEmbeddingModel | None = None
        self._embedding_cache: dict[tuple[str, str], tuple[float, ...]] = {}

    def score(
        self,
        *,
        baseline_output: str | None,
        observed_output: str | None,
    ) -> float | None:
        baseline = normalize_metric_text(baseline_output)
        observed = normalize_metric_text(observed_output)
        if baseline is None or observed is None:
            return None

        baseline_embedding = self._embedding_for_text(baseline)
        observed_embedding = self._embedding_for_text(observed)
        return cosine_similarity(baseline_embedding, observed_embedding)

    def _load_model(self) -> _SentenceEmbeddingModel:
        if self._model is None:
            model = self._model_factory(self._model_name)
            if not hasattr(model, "encode"):
                raise RuntimeError("SBERT model instance must provide an encode(...) method")
            self._model = model
        return self._model

    def _embedding_for_text(self, text: str) -> tuple[float, ...]:
        cache_key = (self._model_name, text_hash(text))
        cached = self._embedding_cache.get(cache_key)
        if cached is not None:
            return cached

        model = self._load_model()
        try:
            raw_embedding = model.encode(text, convert_to_numpy=True)
        except TypeError:
            raw_embedding = model.encode(text)
        embedding = coerce_embedding(raw_embedding)
        self._embedding_cache[cache_key] = embedding
        return embedding


def normalize_metric_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def coerce_embedding(raw: Any) -> tuple[float, ...]:
    value = raw.tolist() if hasattr(raw, "tolist") else raw
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        if value and isinstance(value[0], Sequence) and not isinstance(value[0], str | bytes):
            first = value[0]
            return tuple(float(item) for item in first)
        return tuple(float(item) for item in value)
    raise RuntimeError("SBERT encode(...) must return a numeric vector or vector-like value")


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(x * y for x, y in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return float(dot / (left_norm * right_norm))
