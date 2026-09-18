from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class Chunk:
    document_id: str
    chunk_id: str
    text: str
    title: str = ""
    chapter_id: str = ""
    source_path: str = ""
    knowledge_point_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Chunk":
        return cls(**value)


@dataclass(slots=True)
class RetrievalHit:
    chunk: Chunk
    score: float
    vector_score: float = 0.0
    lexical_score: float = 0.0
    rank: int = 0
    reranker_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_dict(),
            "score": self.score,
            "vector_score": self.vector_score,
            "lexical_score": self.lexical_score,
            "reranker_score": self.reranker_score,
            "rank": self.rank,
        }


@dataclass(slots=True)
class Citation:
    chunk_id: str
    document_id: str
    source_path: str
    title: str
    excerpt: str
    document_name: str = ""
    grade: str = ""
    knowledge_points: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChatResult:
    answer: str
    citations: list[Citation]
    confidence: float
    refused: bool = False
    intent: str = "qa"
    retrieval_hits: list[RetrievalHit] = field(default_factory=list)
    source: str = "knowledge_base"

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": [item.to_dict() for item in self.citations],
            "confidence": self.confidence,
            "refused": self.refused,
            "intent": self.intent,
            "source": self.source,
            "retrieval_hits": [item.to_dict() for item in self.retrieval_hits],
        }
