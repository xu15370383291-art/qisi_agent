"""Student-specific hybrid retrieval for personalized answers.

This module deliberately keeps learning evidence separate from the course
knowledge base.  Course material establishes factual correctness; student
context only helps choose the explanation, examples, and emphasis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Protocol

try:  # PostgreSQL is optional for the in-memory test/development store.
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

from .embeddings import HashEmbedding, cosine, meaningful_overlap
from .memory import MemoryItem, memory_freshness
from .models import Chunk, RetrievalHit
from .reranker import PassthroughReranker, Reranker


ACTIVE_MEMORY_STATUSES = frozenset({"candidate", "active", "updated"})


def embedding_dimensions(embedder: Any) -> int:
    """Get a positive vector dimension from either project embedding adapter."""
    configured = getattr(getattr(embedder, "config", None), "embedding_dimensions", None)
    dimensions = getattr(embedder, "dimensions", configured)
    try:
        dimensions = int(dimensions)
    except (TypeError, ValueError) as exc:
        raise ValueError("学生记忆向量服务缺少有效维度") from exc
    if dimensions <= 0:
        raise ValueError("学生记忆向量维度必须大于 0")
    return dimensions


def embedding_model_name(embedder: Any) -> str:
    return str(getattr(embedder, "model", "unknown"))


def memory_embedding_text(item: MemoryItem | dict[str, Any]) -> str:
    if isinstance(item, MemoryItem):
        knowledge_point, content, status = item.knowledge_point_id, item.content, item.status
    else:
        knowledge_point = str(item.get("knowledge_point_id", ""))
        content = str(item.get("content", ""))
        status = str(item.get("status", ""))
    return "\n".join(part for part in (
        "类型：学习记忆", f"知识点：{knowledge_point}" if knowledge_point else "",
        f"状态：{status}" if status else "", f"内容：{content}",
    ) if part)


def mistake_embedding_text(item: dict[str, Any]) -> str:
    return "\n".join(part for part in (
        "类型：错题记录",
        f"知识点：{item.get('knowledge_point_id', '')}" if item.get("knowledge_point_id") else "",
        f"题目：{item.get('prompt', '')}",
        f"学生答案：{item.get('student_answer', '')}" if item.get("student_answer") else "",
        f"正确答案：{item.get('correct_answer', '')}" if item.get("correct_answer") else "",
        f"解析：{item.get('explanation', '')}" if item.get("explanation") else "",
    ) if part)


def _student_context_schema_sql(dimensions: int) -> str:
    if dimensions <= 0:
        raise ValueError("学生记忆向量维度必须大于 0")
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS public.learning_memory_embeddings (
        memory_id TEXT PRIMARY KEY REFERENCES public.learning_memories(memory_id) ON DELETE CASCADE,
        student_id TEXT NOT NULL REFERENCES public.app_users(user_id) ON DELETE CASCADE,
        embedding vector({dimensions}) NOT NULL,
        embedding_model TEXT NOT NULL,
        embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions = {dimensions}),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS learning_memory_embeddings_student_idx
      ON public.learning_memory_embeddings(student_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS learning_memory_embeddings_hnsw_idx
      ON public.learning_memory_embeddings USING hnsw (embedding vector_cosine_ops);

    CREATE TABLE IF NOT EXISTS public.learning_mistake_embeddings (
        mistake_id TEXT PRIMARY KEY REFERENCES public.learning_mistakes(mistake_id) ON DELETE CASCADE,
        student_id TEXT NOT NULL REFERENCES public.app_users(user_id) ON DELETE CASCADE,
        embedding vector({dimensions}) NOT NULL,
        embedding_model TEXT NOT NULL,
        embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions = {dimensions}),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS learning_mistake_embeddings_student_idx
      ON public.learning_mistake_embeddings(student_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS learning_mistake_embeddings_hnsw_idx
      ON public.learning_mistake_embeddings USING hnsw (embedding vector_cosine_ops);
    """


def ensure_student_context_embedding_schema(database_url: str, embedder: Any) -> None:
    """Create pgvector tables used only by the student-context retrieval path."""
    if psycopg is None:  # pragma: no cover
        raise RuntimeError("PostgreSQL 学生记忆检索需要安装 psycopg")
    with psycopg.connect(database_url) as conn:
        conn.execute(_student_context_schema_sql(embedding_dimensions(embedder)))


def _vector_literal(vector: Iterable[float]) -> str:
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("学生记忆 Embedding 必须是有效非空向量")
    return "[" + ",".join(map(str, values)) + "]"


def upsert_student_context_embedding(database_url: str, *, source_type: str,
                                     source_id: str, student_id: str, text: str,
                                     embedder: Any) -> None:
    """Embed one memory or mistake after it has been durably written."""
    table, id_column = {
        "memory": ("learning_memory_embeddings", "memory_id"),
        "mistake": ("learning_mistake_embeddings", "mistake_id"),
    }.get(source_type, (None, None))
    if table is None or id_column is None:
        raise ValueError("source_type 必须是 memory 或 mistake")
    ensure_student_context_embedding_schema(database_url, embedder)
    vector = _vector_literal(embedder.embed(text))
    dimensions, model = embedding_dimensions(embedder), embedding_model_name(embedder)
    with psycopg.connect(database_url) as conn:
        conn.execute(
            f"""INSERT INTO {table}({id_column}, student_id, embedding, embedding_model, embedding_dimensions)
                VALUES (%s,%s,%s::vector,%s,%s)
                ON CONFLICT ({id_column}) DO UPDATE SET
                  student_id=EXCLUDED.student_id, embedding=EXCLUDED.embedding,
                  embedding_model=EXCLUDED.embedding_model,
                  embedding_dimensions=EXCLUDED.embedding_dimensions, updated_at=NOW()""",
            (source_id, student_id, vector, model, dimensions),
        )


def sync_student_context_embeddings(database_url: str, embedder: Any, *, force: bool = False) -> dict[str, int | str]:
    """Backfill missing or model-stale memory and mistake vectors in batches."""
    ensure_student_context_embedding_schema(database_url, embedder)
    model = embedding_model_name(embedder)
    dimensions = embedding_dimensions(embedder)
    sources = (
        ("memory", "learning_memory_embeddings", "memory_id", "learning_memories",
         "source.memory_id, source.student_id, source.content, source.knowledge_point_id, source.status"),
        ("mistake", "learning_mistake_embeddings", "mistake_id", "learning_mistakes",
         "source.mistake_id, source.student_id, source.prompt, source.student_answer, source.correct_answer, source.explanation, source.knowledge_point_id, source.status"),
    )
    result: dict[str, int | str] = {"memory_embedded": 0, "mistake_embedded": 0,
                                     "embedding_model": model, "dimensions": dimensions}
    for source_type, vector_table, id_column, source_table, columns in sources:
        with psycopg.connect(database_url) as conn:
            rows = conn.execute(
                f"""SELECT {columns} FROM {source_table} source
                    LEFT JOIN {vector_table} vector ON vector.{id_column}=source.{id_column}
                    WHERE %s OR vector.{id_column} IS NULL OR vector.embedding_model<>%s
                    ORDER BY source.{id_column}""",
                (force, model),
            ).fetchall()
        for row in rows:
            if source_type == "memory":
                item = {"memory_id": row[0], "student_id": row[1], "content": row[2],
                        "knowledge_point_id": row[3], "status": row[4]}
                source_id, student_id, text = row[0], row[1], memory_embedding_text(item)
            else:
                item = {"mistake_id": row[0], "student_id": row[1], "prompt": row[2],
                        "student_answer": row[3], "correct_answer": row[4], "explanation": row[5],
                        "knowledge_point_id": row[6], "status": row[7]}
                source_id, student_id, text = row[0], row[1], mistake_embedding_text(item)
            upsert_student_context_embedding(database_url, source_type=source_type,
                                             source_id=source_id, student_id=student_id,
                                             text=text, embedder=embedder)
            result[f"{source_type}_embedded"] = int(result[f"{source_type}_embedded"]) + 1
    return result


def _freshness(value: Any, half_life_days: float = 45.0) -> float:
    if not value:
        return 1.0
    try:
        if isinstance(value, str):
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            observed = value
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - observed).total_seconds() / 86400)
        return 2 ** (-age_days / half_life_days)
    except (TypeError, ValueError, OverflowError):
        return 1.0


def _rrf_score(vector_rank: int | None, exact_rank: int | None) -> float:
    return (1 / (60 + vector_rank) if vector_rank else 0.0) + (1 / (60 + exact_rank) if exact_rank else 0.0)


@dataclass(slots=True)
class StudentContext:
    memory_hits: list[RetrievalHit] = field(default_factory=list)
    mistake_hits: list[RetrievalHit] = field(default_factory=list)
    practice_evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_textual_evidence(self) -> bool:
        return bool(self.memory_hits or self.mistake_hits)

    @property
    def has_relevant_evidence(self) -> bool:
        return self.has_textual_evidence or bool(self.practice_evidence)


class _MemoryStoreProtocol(Protocol):
    def list_for_student(self, student_id: str, *, include_deleted: bool = False) -> list[MemoryItem]: ...
    def list_mistakes(self, student_id: str, *, include_reviewed: bool = True) -> list[dict[str, Any]]: ...
    def mastery(self, student_id: str) -> list[dict[str, Any]]: ...


class StudentContextRetriever:
    """Hybrid retriever for one student's learning evidence.

    It supports the lightweight in-memory store for tests and the PostgreSQL
    store in production.  PostgreSQL vector candidates are retrieved from
    dedicated, student-scoped pgvector tables; exact candidates are evaluated
    independently and then fused with RRF before reranking.
    """

    def __init__(self, memory: _MemoryStoreProtocol, *, embedder: Any | None = None,
                 reranker: Reranker | None = None):
        self.memory = memory
        self.embedder = embedder or getattr(memory, "embedding", None) or HashEmbedding()
        self.reranker = reranker or PassthroughReranker()
        self.database_url = getattr(memory, "database_url", None)

    @staticmethod
    def _valid_memory(item: MemoryItem) -> bool:
        return item.status in ACTIVE_MEMORY_STATUSES

    @staticmethod
    def _exact_score(query: str, text: str, knowledge_point: str) -> float:
        score = float(meaningful_overlap(query, " ".join((knowledge_point, text))))
        compact = "".join(query.lower().split())
        point = "".join(knowledge_point.lower().split())
        if point and point in compact:
            score += 30 + len(point)
        return score

    def _postgres_vector_scores(self, *, source_type: str, student_id: str,
                                query_vector: list[float], limit: int) -> dict[str, float]:
        if not self.database_url or psycopg is None:
            return {}
        table, id_column, source_table, status_clause = {
            "memory": ("learning_memory_embeddings", "memory_id", "learning_memories",
                       "source.status IN ('candidate','active','updated')"),
            "mistake": ("learning_mistake_embeddings", "mistake_id", "learning_mistakes", "TRUE"),
        }[source_type]
        try:
            with psycopg.connect(self.database_url) as conn:
                rows = conn.execute(
                    f"""SELECT vector.{id_column}, 1-(vector.embedding <=> %s::vector) AS score
                        FROM {table} vector
                        JOIN {source_table} source ON source.{id_column}=vector.{id_column}
                        WHERE vector.student_id=%s AND source.student_id=%s AND {status_clause}
                        ORDER BY vector.embedding <=> %s::vector
                        LIMIT %s""",
                    (_vector_literal(query_vector), student_id, student_id,
                     _vector_literal(query_vector), max(1, limit)),
                ).fetchall()
            return {str(source_id): float(score) for source_id, score in rows}
        except psycopg.errors.UndefinedTable:
            return {}

    def _build_hits(self, *, source_type: str, student_id: str, query: str,
                    rows: list[MemoryItem] | list[dict[str, Any]], top_k: int) -> list[RetrievalHit]:
        if source_type == "memory":
            source_rows = [item for item in rows if isinstance(item, MemoryItem) and self._valid_memory(item)]
            ids = [item.memory_id for item in source_rows]
            texts = {item.memory_id: memory_embedding_text(item) for item in source_rows}
            points = {item.memory_id: item.knowledge_point_id for item in source_rows}
            dates = {item.memory_id: item.occurred_at for item in source_rows}
            reinforced_dates = {item.memory_id: item.last_reinforced_at for item in source_rows}
            memory_types = {item.memory_id: item.memory_type for item in source_rows}
            confidence = {item.memory_id: float(item.confidence) for item in source_rows}
            importance = {item.memory_id: float(item.importance) for item in source_rows}
            raw_content = {item.memory_id: item.content for item in source_rows}
            statuses = {item.memory_id: item.status for item in source_rows}
        else:
            source_rows = [item for item in rows if isinstance(item, dict)]
            ids = [str(item["mistake_id"]) for item in source_rows]
            texts = {str(item["mistake_id"]): mistake_embedding_text(item) for item in source_rows}
            points = {str(item["mistake_id"]): str(item.get("knowledge_point_id", "")) for item in source_rows}
            dates = {str(item["mistake_id"]): item.get("created_at", "") for item in source_rows}
            confidence = {source_id: 0.92 for source_id in ids}
            importance = {source_id: 0.9 if item.get("status") != "reviewed" else 0.7
                          for source_id, item in ((str(value["mistake_id"]), value) for value in source_rows)}
            raw_content = {str(item["mistake_id"]): str(item.get("explanation") or item.get("prompt") or "") for item in source_rows}
            statuses = {source_id: "active" for source_id in ids}
            reinforced_dates = {source_id: "" for source_id in ids}
            memory_types = {source_id: "learning_event" for source_id in ids}
        if not ids:
            return []

        exact_scores = {source_id: self._exact_score(query, texts[source_id], points[source_id]) for source_id in ids}
        exact_ids = sorted((source_id for source_id in ids if exact_scores[source_id] > 0),
                           key=lambda source_id: (-exact_scores[source_id], source_id))[:max(top_k * 4, top_k)]
        vector_scores: dict[str, float]
        try:
            query_vector = self.embedder.embed(query)
            vector_scores = self._postgres_vector_scores(source_type=source_type, student_id=student_id,
                                                         query_vector=query_vector, limit=max(top_k * 4, top_k))
            if not vector_scores:
                vector_scores = {source_id: cosine(query_vector, self.embedder.embed(texts[source_id])) for source_id in ids}
        except Exception:
            # Exact search still provides a safe route if an embedding provider is temporarily unavailable.
            vector_scores = {}
        vector_ids = sorted(vector_scores, key=vector_scores.get, reverse=True)[:max(top_k * 4, top_k)]
        vector_rank = {source_id: rank for rank, source_id in enumerate(vector_ids, 1)}
        exact_rank = {source_id: rank for rank, source_id in enumerate(exact_ids, 1)}
        candidate_ids = list(dict.fromkeys([*exact_ids, *vector_ids]))
        hits: list[RetrievalHit] = []
        for source_id in candidate_ids:
            if not raw_content[source_id]:
                continue
            chunk = Chunk(
                document_id=f"student-{source_type}", chunk_id=source_id,
                text=raw_content[source_id], title="学习记忆" if source_type == "memory" else "相关错题",
                chapter_id=points[source_id], knowledge_point_ids=[points[source_id]] if points[source_id] else [],
                metadata={"source_type": f"student_{source_type}", "student_id": student_id,
                          "status": statuses[source_id], "confidence": confidence[source_id],
                          "importance": importance[source_id], "occurred_at": str(dates[source_id]),
                          "last_reinforced_at": str(reinforced_dates[source_id]), "memory_type": memory_types[source_id],
                          "exact_score": exact_scores[source_id]},
            )
            hits.append(RetrievalHit(chunk=chunk,
                                     score=_rrf_score(vector_rank.get(source_id), exact_rank.get(source_id)),
                                     vector_score=vector_scores.get(source_id, 0.0),
                                     lexical_score=exact_scores[source_id]))
        if not hits:
            return []
        ranked = self.reranker.rerank(query, hits)
        for hit in ranked:
            metadata = hit.chunk.metadata
            # The reranker judges relevance only.  Evidence reliability and recency remain business rules.
            relevance = hit.reranker_score if hit.reranker_score else max(hit.score, 0.01)
            state_weight = 0.72 if metadata["status"] == "candidate" else 1.0
            hit.score = relevance * float(metadata["confidence"]) * float(metadata["importance"]) * \
                        memory_freshness(str(metadata.get("memory_type", "learning_event")),
                                         str(metadata["occurred_at"]), str(metadata.get("last_reinforced_at", ""))) * state_weight
        ranked.sort(key=lambda hit: hit.score, reverse=True)
        for rank, hit in enumerate(ranked[:top_k], 1):
            hit.rank = rank
        return ranked[:top_k]

    def _practice_evidence(self, student_id: str, query: str, *, top_k: int) -> list[dict[str, Any]]:
        rows = []
        for item in self.memory.mastery(student_id):
            point = str(item.get("knowledge_point", ""))
            if not point:
                continue
            relevance = self._exact_score(query, point, point)
            if relevance <= 0:
                continue
            rows.append({"source_type": "practice", "knowledge_point_id": point,
                         "mastery_score": item.get("score"), "mastery_status": item.get("status"),
                         "attempts": item.get("attempts", 0), "correct": item.get("correct", 0),
                         "mistakes": item.get("mistakes", 0), "relevance": relevance})
        rows.sort(key=lambda item: (-float(item["relevance"]), float(item.get("mastery_score", 50))))
        return rows[:top_k]

    def retrieve(self, student_id: str, query: str, *, top_k: int = 4) -> StudentContext:
        query = query.strip()
        if not query or not student_id:
            return StudentContext()
        return StudentContext(
            memory_hits=self._build_hits(source_type="memory", student_id=student_id, query=query,
                                         rows=self.memory.list_for_student(student_id), top_k=top_k),
            mistake_hits=self._build_hits(source_type="mistake", student_id=student_id, query=query,
                                          rows=self.memory.list_mistakes(student_id), top_k=top_k),
            practice_evidence=self._practice_evidence(student_id, query, top_k=top_k),
        )
