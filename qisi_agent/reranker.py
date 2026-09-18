from __future__ import annotations

import math
import os
import warnings
from typing import Protocol, Sequence

from .models import RetrievalHit


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, hits: Sequence[RetrievalHit]) -> list[RetrievalHit]:
        ...


class PassthroughReranker:
    """Keep the hybrid retrieval order when an optional model is unavailable."""

    name = "disabled"

    def rerank(self, query: str, hits: Sequence[RetrievalHit]) -> list[RetrievalHit]:
        del query
        return sorted(hits, key=lambda item: item.score, reverse=True)


class CrossEncoderReranker:
    """Model-backed reranker scoring each query/document pair jointly."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", *,
                 device: str | None = None, batch_size: int = 16,
                 max_length: int = 512, model=None, cache_folder: str | None = None,
                 local_files_only: bool = False):
        if batch_size < 1:
            raise ValueError("RERANKER_BATCH_SIZE 必须大于 0")
        if max_length < 1:
            raise ValueError("RERANKER_MAX_LENGTH 必须大于 0")
        self.model_name = model_name
        self.name = f"cross-encoder:{model_name}"
        self.batch_size = batch_size
        self.max_length = max_length
        if model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "Cross-Encoder reranker 依赖尚未安装，请运行 "
                    "python -m pip install -e \".[reranker]\""
                ) from exc
            try:
                model = CrossEncoder(model_name, device=device, max_length=max_length,
                                     cache_folder=cache_folder, local_files_only=local_files_only)
            except Exception as exc:
                raise RuntimeError(
                    f"无法加载 Cross-Encoder reranker {model_name!r}；"
                    "请检查 RERANKER_MODEL、网络或本地模型缓存"
                ) from exc
        self.model = model

    @staticmethod
    def _document_text(hit: RetrievalHit) -> str:
        chunk = hit.chunk
        fields = [
            f"标题：{chunk.title}" if chunk.title else "",
            f"章节：{chunk.chapter_id}" if chunk.chapter_id else "",
            f"知识点：{'、'.join(chunk.knowledge_point_ids)}" if chunk.knowledge_point_ids else "",
            chunk.text,
        ]
        return "\n".join(field for field in fields if field)

    @staticmethod
    def _as_scores(values, expected: int) -> list[float]:
        if hasattr(values, "tolist"):
            values = values.tolist()
        if expected == 1 and not isinstance(values, (list, tuple)):
            values = [values]
        scores: list[float] = []
        for value in values:
            if isinstance(value, (list, tuple)):
                if len(value) != 1:
                    raise ValueError("Cross-Encoder 必须输出单个相关性分数")
                value = value[0]
            scores.append(float(value))
        if len(scores) != expected:
            raise ValueError(f"Cross-Encoder 返回 {len(scores)} 个分数，期望 {expected} 个")
        if any(not math.isfinite(score) for score in scores):
            raise ValueError("Cross-Encoder 返回了非有限分数")
        # Some CrossEncoder versions return logits while others apply sigmoid
        # for single-label models. Normalize logits only when the batch proves
        # that the values are not already probabilities.
        if any(score < 0.0 or score > 1.0 for score in scores):
            scores = [1.0 / (1.0 + math.exp(-max(-709.0, min(709.0, score))))
                      for score in scores]
        return scores

    def rerank(self, query: str, hits: Sequence[RetrievalHit]) -> list[RetrievalHit]:
        ranked = list(hits)
        if not ranked:
            return ranked
        pairs = [(query, self._document_text(hit)) for hit in ranked]
        values = self.model.predict(pairs, batch_size=self.batch_size,
                                    show_progress_bar=False, convert_to_numpy=True)
        scores = self._as_scores(values, len(ranked))
        for hit, score in zip(ranked, scores):
            hit.chunk.metadata["retrieval_score"] = hit.score
            hit.chunk.metadata["reranker"] = self.name
            hit.reranker_score = score
            hit.score = score
        ranked.sort(key=lambda item: item.score, reverse=True)
        return ranked


def cross_encoder_from_env() -> Reranker:
    """Build the production reranker from environment configuration."""
    enabled = os.getenv("RERANKER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    required = os.getenv("RERANKER_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return PassthroughReranker()
    try:
        return CrossEncoderReranker(
            model_name=os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-base"),
            device=os.getenv("RERANKER_DEVICE") or None,
            batch_size=int(os.getenv("RERANKER_BATCH_SIZE", "16")),
            max_length=int(os.getenv("RERANKER_MAX_LENGTH", "512")),
            cache_folder=os.getenv("RERANKER_CACHE_DIR") or None,
            local_files_only=os.getenv("RERANKER_LOCAL_FILES_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"},
        )
    except (ImportError, RuntimeError, OSError, ValueError) as exc:
        if required:
            raise
        warnings.warn(
            f"Cross-Encoder reranker unavailable ({exc}); continuing without model reranking. "
            "Set RERANKER_REQUIRED=true to make this a startup error.",
            RuntimeWarning,
            stacklevel=2,
        )
        return PassthroughReranker()
