from __future__ import annotations

"""Normalized PostgreSQL knowledge-base storage and deterministic search."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

from .embeddings import meaningful_overlap
from .ingestion import load_corpus


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS public.knowledge_documents (
    document_id TEXT PRIMARY KEY,
    document_name TEXT NOT NULL,
    grade_id TEXT NOT NULL CHECK (grade_id IN ('grade7', 'grade8', 'grade9')),
    grade_name TEXT NOT NULL,
    source_path TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.knowledge_documents
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'published',
  ADD COLUMN IF NOT EXISTS owner_id TEXT,
  ADD COLUMN IF NOT EXISTS document_type TEXT NOT NULL DEFAULT '教材',
  ADD COLUMN IF NOT EXISTS current_version_id TEXT;

CREATE TABLE IF NOT EXISTS public.knowledge_document_versions (
    version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES public.knowledge_documents(document_id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, file_hash)
);

CREATE TABLE IF NOT EXISTS public.ingestion_jobs (
    job_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES public.knowledge_document_versions(version_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'uploaded',
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ingestion_jobs_queue_idx
  ON public.ingestion_jobs (status, created_at);

CREATE TABLE IF NOT EXISTS public.knowledge_contents (
    content_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES public.knowledge_documents(document_id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_chunk_id TEXT NOT NULL UNIQUE,
    version_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.knowledge_contents
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS version_id TEXT;

CREATE TABLE IF NOT EXISTS public.knowledge_points (
    knowledge_point_id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL REFERENCES public.knowledge_contents(content_id) ON DELETE CASCADE,
    point_name TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    UNIQUE (content_id, point_name)
);

CREATE INDEX IF NOT EXISTS knowledge_documents_grade_idx
    ON public.knowledge_documents (grade_id);
CREATE INDEX IF NOT EXISTS knowledge_contents_document_idx
    ON public.knowledge_contents (document_id);
CREATE INDEX IF NOT EXISTS knowledge_points_name_idx
    ON public.knowledge_points (point_name);
CREATE INDEX IF NOT EXISTS knowledge_documents_status_idx
    ON public.knowledge_documents (status);
CREATE INDEX IF NOT EXISTS knowledge_documents_current_version_idx
    ON public.knowledge_documents (current_version_id);
CREATE INDEX IF NOT EXISTS knowledge_contents_version_idx
    ON public.knowledge_contents (version_id);
CREATE INDEX IF NOT EXISTS knowledge_document_versions_document_created_idx
    ON public.knowledge_document_versions (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_jobs_status_created_idx
  ON public.ingestion_jobs (status, created_at DESC);

CREATE OR REPLACE VIEW public.admin_content_imports AS
SELECT j.job_id,
       j.status,
       j.stage,
       j.progress,
       j.retry_count,
       j.error_message,
       j.created_by,
       j.created_at,
       j.started_at,
       j.finished_at,
       v.version_id,
       v.document_id,
       v.file_name,
       v.file_hash,
       v.status AS version_status,
       d.document_name,
       d.document_type,
       d.grade_id,
       d.grade_name
FROM public.ingestion_jobs j
JOIN public.knowledge_document_versions v USING (version_id)
JOIN public.knowledge_documents d USING (document_id);

CREATE OR REPLACE VIEW public.knowledge_point_search AS
SELECT p.knowledge_point_id, p.point_name, p.ordinal,
       c.content_id, c.title, c.chapter_id, c.content,
       d.document_id, d.document_name, d.grade_id, d.grade_name, d.source_path
FROM public.knowledge_points p
JOIN public.knowledge_contents c ON c.content_id = p.content_id
JOIN public.knowledge_documents d ON d.document_id = c.document_id;

CREATE OR REPLACE VIEW public.knowledge_grade7 AS
SELECT * FROM public.knowledge_point_search WHERE grade_id = 'grade7';
CREATE OR REPLACE VIEW public.knowledge_grade8 AS
SELECT * FROM public.knowledge_point_search WHERE grade_id = 'grade8';
CREATE OR REPLACE VIEW public.knowledge_grade9 AS
SELECT * FROM public.knowledge_point_search WHERE grade_id = 'grade9';
"""


def _embedding_schema_sql(dimensions: int) -> str:
    if not isinstance(dimensions, int) or dimensions <= 0:
        raise ValueError("Embedding 维度必须是正整数")
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS public.knowledge_content_embeddings (
        content_id TEXT PRIMARY KEY REFERENCES public.knowledge_contents(content_id) ON DELETE CASCADE,
        embedding vector({dimensions}) NOT NULL,
        embedding_model TEXT NOT NULL,
        embedding_dimensions INTEGER NOT NULL CHECK (embedding_dimensions = {dimensions}),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS knowledge_content_embeddings_hnsw_idx
      ON public.knowledge_content_embeddings USING hnsw (embedding vector_cosine_ops);
    """


def _point_id(document_id: str, content_id: str, ordinal: int, name: str) -> str:
    raw = f"{document_id}\0{content_id}\0{ordinal}\0{name}".encode("utf-8")
    return "kp_" + hashlib.sha1(raw).hexdigest()[:16]


def _ordered_subsequence(needle: str, haystack: str) -> bool:
    """Match a Chinese concept when natural-language filler occurs between terms."""
    if not needle:
        return False
    position = 0
    for char in needle:
        position = haystack.find(char, position)
        if position < 0:
            return False
        position += 1
    return True


def sync_corpus(database_url: str, source: str = "data/corpus") -> dict[str, Any]:
    """Create normalized tables and upsert the local corpus without duplicating content."""
    chunks = load_corpus(source)
    documents: dict[str, tuple[str, str, str, str, str, str, str]] = {}
    versions: dict[str, tuple[str, str, str, str, str]] = {}
    for chunk in chunks:
        grade_id = str(chunk.metadata.get("grade_id") or "")
        grade_name = str(chunk.metadata.get("grade") or "")
        if grade_id not in {"grade7", "grade8", "grade9"}:
            raise ValueError(f"无法识别年级: {chunk.source_path}")
        path = Path(chunk.source_path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        version_id = "ver_" + digest[:20]
        document_type = "题库" if path.suffix.lower() in {".json", ".jsonl", ".csv"} else "教材"
        documents[chunk.document_id] = (chunk.document_id, path.name, grade_id,
                                         grade_name, path.as_posix(), document_type, None)
        versions[chunk.document_id] = (version_id, chunk.document_id, path.name,
                                       path.as_posix(), digest)
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.executemany(
                """INSERT INTO knowledge_documents
                   (document_id, document_name, grade_id, grade_name, source_path,
                    document_type, current_version_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (document_id) DO UPDATE SET
                     document_name=EXCLUDED.document_name, grade_id=EXCLUDED.grade_id,
                     grade_name=EXCLUDED.grade_name, source_path=EXCLUDED.source_path,
                     document_type=EXCLUDED.document_type,
                     updated_at=NOW()""",
                list(documents.values()),
            )
            cur.executemany(
                """INSERT INTO knowledge_document_versions
                   (version_id, document_id, file_name, storage_path, file_hash, status)
                   VALUES (%s,%s,%s,%s,%s,'published')
                   ON CONFLICT (version_id) DO UPDATE SET
                     file_name=EXCLUDED.file_name, storage_path=EXCLUDED.storage_path,
                     file_hash=EXCLUDED.file_hash, status='published'""",
                list(versions.values()),
            )
            cur.executemany(
                "UPDATE knowledge_documents SET current_version_id=%s, updated_at=NOW() WHERE document_id=%s",
                [(item[0], item[1]) for item in versions.values()],
            )
            for chunk in chunks:
                version_id = versions[chunk.document_id][0]
                cur.execute(
                    """INSERT INTO knowledge_contents
                       (content_id, document_id, chapter_id, title, content, metadata,
                        source_chunk_id, version_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (content_id) DO UPDATE SET
                         document_id=EXCLUDED.document_id, chapter_id=EXCLUDED.chapter_id,
                         title=EXCLUDED.title, content=EXCLUDED.content, metadata=EXCLUDED.metadata,
                         source_chunk_id=EXCLUDED.source_chunk_id, version_id=EXCLUDED.version_id,
                         updated_at=NOW()""",
                    (chunk.chunk_id, chunk.document_id, chunk.chapter_id, chunk.title,
                     chunk.text, json.dumps(chunk.metadata, ensure_ascii=False), chunk.chunk_id,
                     version_id),
                )
                cur.execute("DELETE FROM knowledge_points WHERE content_id = %s", (chunk.chunk_id,))
                cur.executemany(
                    "INSERT INTO knowledge_points (knowledge_point_id, content_id, point_name, ordinal) VALUES (%s,%s,%s,%s)",
                    [(_point_id(chunk.document_id, chunk.chunk_id, i, name), chunk.chunk_id, name, i)
                     for i, name in enumerate(chunk.knowledge_point_ids, 1)],
                )
            # Remove records for files no longer in the local corpus.
            ids = [chunk.chunk_id for chunk in chunks]
            cur.execute("DELETE FROM knowledge_contents WHERE NOT (content_id = ANY(%s))", (ids,))
            cur.execute("DELETE FROM knowledge_documents WHERE NOT (document_id = ANY(%s))", (list(documents),))
            cur.execute("SELECT COUNT(*) FROM knowledge_documents")
            document_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM knowledge_contents")
            content_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM knowledge_points")
            point_count = cur.fetchone()[0]
    return {"documents": document_count, "contents": content_count, "knowledge_points": point_count}


def upsert_chunks(database_url: str, chunks: list[Any], *, owner_id: str | None = None,
                  status: str = "published", version_ids: dict[str, str] | None = None) -> dict[str, int]:
    """Upsert parsed content without deleting documents outside this batch."""
    if not chunks:
        raise ValueError("没有可写入的知识内容")
    documents: dict[str, tuple[str, str, str, str, str, str, str | None, str]] = {}
    for chunk in chunks:
        grade_id = str(chunk.metadata.get("grade_id") or "")
        grade_name = str(chunk.metadata.get("grade") or "")
        if grade_id not in {"grade7", "grade8", "grade9"}:
            raise ValueError(f"无法识别年级: {chunk.source_path}")
        path = chunk.source_path
        document_type = "题库" if Path(path).suffix.lower() in {".json", ".jsonl", ".csv"} else "教材"
        documents[chunk.document_id] = (chunk.document_id, path.rsplit("/", 1)[-1], grade_id,
                                         grade_name, path, document_type, owner_id, status)
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.executemany("""
          INSERT INTO knowledge_documents
            (document_id, document_name, grade_id, grade_name, source_path,
             document_type, owner_id, status)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (document_id) DO UPDATE SET
            document_name=EXCLUDED.document_name, grade_id=EXCLUDED.grade_id,
            grade_name=EXCLUDED.grade_name, source_path=EXCLUDED.source_path,
            document_type=EXCLUDED.document_type,
            owner_id=COALESCE(EXCLUDED.owner_id, knowledge_documents.owner_id),
            status=EXCLUDED.status, updated_at=NOW()
        """, list(documents.values()))
        for chunk in chunks:
            cur.execute("""
            INSERT INTO knowledge_contents
                (content_id, document_id, chapter_id, title, content, metadata, source_chunk_id, version_id)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (content_id) DO UPDATE SET
                document_id=EXCLUDED.document_id, chapter_id=EXCLUDED.chapter_id,
                title=EXCLUDED.title, content=EXCLUDED.content, metadata=EXCLUDED.metadata,
                source_chunk_id=EXCLUDED.source_chunk_id, version_id=EXCLUDED.version_id,
                updated_at=NOW()
            """, (chunk.chunk_id, chunk.document_id, chunk.chapter_id, chunk.title,
                   chunk.text, json.dumps(chunk.metadata, ensure_ascii=False), chunk.chunk_id,
                   (version_ids or {}).get(chunk.document_id)))
            cur.execute("DELETE FROM knowledge_points WHERE content_id = %s", (chunk.chunk_id,))
            cur.executemany(
                "INSERT INTO knowledge_points (knowledge_point_id, content_id, point_name, ordinal) VALUES (%s,%s,%s,%s)",
                [(_point_id(chunk.document_id, chunk.chunk_id, i, name), chunk.chunk_id, name, i)
                 for i, name in enumerate(chunk.knowledge_point_ids, 1)],
            )
        return {"documents": len(documents), "contents": len(chunks),
                "knowledge_points": sum(len(chunk.knowledge_point_ids) for chunk in chunks)}


def embedding_text(content_id: str, database_url: str) -> str:
    """Build the stable text representation used for a content embedding."""
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("""
          SELECT c.title, c.chapter_id, c.content, d.grade_name,
                 COALESCE(string_agg(p.point_name, '、' ORDER BY p.ordinal), '')
          FROM knowledge_contents c
          JOIN knowledge_documents d USING(document_id)
          LEFT JOIN knowledge_points p USING(content_id)
          WHERE c.content_id = %s
          GROUP BY c.content_id, c.title, c.chapter_id, c.content, d.grade_name
        """, (content_id,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"找不到 content_id: {content_id}")
    title, chapter, content, grade, points = row
    return "\n".join((f"年级：{grade}", f"标题：{title}", f"章节：{chapter}",
                       f"知识点：{points}", "正文：", content))


def embed_corpus(database_url: str, *, model: str, dimensions: int,
                 batch_size: int = 10, force: bool = False) -> dict[str, Any]:
    """Generate and upsert one vector per knowledge content."""
    from .dashscope import DashScopeConfig, DashScopeEmbeddingService

    config = DashScopeConfig.from_env()
    if config.embedding_model != model or config.embedding_dimensions != dimensions:
        raise ValueError("传入的 Embedding 模型/维度与环境配置不一致")
    embedder = DashScopeEmbeddingService(config)
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(_embedding_schema_sql(dimensions))
        sql = """
          SELECT c.content_id
          FROM knowledge_contents c
          LEFT JOIN knowledge_content_embeddings e ON e.content_id = c.content_id
          WHERE %s OR e.content_id IS NULL OR e.embedding_model <> %s
          ORDER BY c.content_id
        """
        cur.execute(sql, (force, model))
        content_ids = [row[0] for row in cur.fetchall()]
    texts = [embedding_text(content_id, database_url) for content_id in content_ids]
    vectors = []
    for start in range(0, len(texts), max(1, min(batch_size, 10))):
        vectors.extend(embedder.embed_texts(texts[start:start + max(1, min(batch_size, 10))]))
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.executemany("""
          INSERT INTO knowledge_content_embeddings
            (content_id, embedding, embedding_model, embedding_dimensions)
          VALUES (%s, %s::vector, %s, %s)
          ON CONFLICT (content_id) DO UPDATE SET
            embedding=EXCLUDED.embedding, embedding_model=EXCLUDED.embedding_model,
            embedding_dimensions=EXCLUDED.embedding_dimensions, updated_at=NOW()
        """, [(content_id, "[" + ",".join(map(str, vector)) + "]", model, dimensions)
               for content_id, vector in zip(content_ids, vectors)])
        cur.execute("SELECT COUNT(*) FROM knowledge_content_embeddings")
        total = cur.fetchone()[0]
    return {"embedded_now": len(content_ids), "embedding_total": total,
            "embedding_model": model, "dimensions": dimensions}


def search(database_url: str, query: str, grade_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return deduplicated content sections with matched points and provenance."""
    sql = "SELECT * FROM knowledge_point_search"
    params: list[Any] = []
    if grade_id:
        sql += " WHERE grade_id = %s"
        params.append(grade_id)
    sql += " ORDER BY document_id, content_id, ordinal"
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [d.name for d in cur.description]
    query = query.strip()
    if not query:
        return []
    compact_query = "".join(query.lower().split())
    grouped: dict[str, dict[str, Any]] = {}
    for values in rows:
        row = dict(zip(columns, values))
        key = row["content_id"]
        item = grouped.setdefault(key, {"content_id": key, "document_id": row["document_id"],
            "document_name": row["document_name"], "source_path": row["source_path"],
            "grade_id": row["grade_id"], "grade_name": row["grade_name"],
            "title": row["title"], "chapter_id": row["chapter_id"], "content": row["content"],
            "knowledge_points": [], "_point_candidates": []})
        point = row["point_name"]
        compact_point = "".join(point.lower().split())
        compact_title = "".join((row["title"] or "").lower().split())
        point_exact = bool(compact_point and (
            compact_point in compact_query or _ordered_subsequence(compact_point, compact_query)))
        title_exact = bool(compact_title and (
            compact_title in compact_query or _ordered_subsequence(compact_title, compact_query)))
        point_score = meaningful_overlap(query, point) * 3
        title_score = meaningful_overlap(query, row["title"]) * 2
        chapter_score = meaningful_overlap(query, row["chapter_id"])
        score = point_score + title_score + chapter_score
        if point_exact:
            # Longer concepts are more specific than their component words
            # (e.g. “分数约分” outranks “分数”).
            score += 1000 + len(compact_point) * 10
        elif title_exact:
            score += 500 + len(compact_title) * 5
        if score:
            item["_point_candidates"].append({
                "knowledge_point_id": row["knowledge_point_id"],
                "name": point,
                "score": score,
                "point_exact": point_exact,
                "title_exact": title_exact,
            })

    # Keep each content section only once and retain its most relevant point(s).
    # This avoids returning every point listed in a section merely because one
    # of them matched the question.
    result = []
    for item in grouped.values():
        candidates = item.pop("_point_candidates")
        best = max((candidate["score"] for candidate in candidates), default=0)
        selected = [candidate for candidate in candidates if candidate["score"] == best and best > 0]
        if not selected:
            continue
        item["knowledge_points"] = [
            {"knowledge_point_id": candidate["knowledge_point_id"], "name": candidate["name"]}
            for candidate in selected
        ]
        item["score"] = max(candidate["score"] for candidate in selected)
        result.append(item)
    result.sort(key=lambda item: (-item["score"], item["grade_id"], item["content_id"]))
    return result[: max(1, limit)]


def database_url_from_env() -> str:
    load_dotenv(".env", override=False)
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL 未配置")
    return value
