from __future__ import annotations

import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any

import psycopg

from .ingestion import _document_id, parse_markdown, parse_question_bank
from .dashscope import DashScopeConfig
from .pg_knowledge import SCHEMA_SQL, database_url_from_env, embed_corpus, upsert_chunks


GRADE_NAMES = {"grade7": "七年级", "grade8": "八年级", "grade9": "九年级"}
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".jsonl", ".csv"}


def enqueue_upload(database_url: str, file_name: str, payload: bytes, grade_id: str,
                   created_by: str, upload_root: str | Path = "data/uploads") -> dict[str, str]:
    """Persist an uploaded file and enqueue durable background processing."""
    if grade_id not in GRADE_NAMES:
        raise ValueError("grade_id 必须是 grade7、grade8 或 grade9")
    safe_name = Path(file_name or "upload.md").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("仅支持 Markdown、TXT、JSON、JSONL 和 CSV 文件")
    if not payload:
        raise ValueError("上传文件不能为空")
    digest = hashlib.sha256(payload).hexdigest()
    job_id = "job_" + secrets.token_urlsafe(12)
    root = Path(upload_root)
    root.mkdir(parents=True, exist_ok=True)
    storage_path = root / f"{job_id}_{safe_name}"
    storage_path.write_bytes(payload)
    document_id = _document_id(storage_path)
    version_id = "ver_" + digest[:20]
    source_path = storage_path.as_posix()
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("""
          INSERT INTO knowledge_documents
            (document_id, document_name, grade_id, grade_name, source_path, owner_id, status)
          VALUES (%s,%s,%s,%s,%s,%s,'processing')
          ON CONFLICT (document_id) DO UPDATE SET
            document_name=EXCLUDED.document_name, grade_id=EXCLUDED.grade_id,
            grade_name=EXCLUDED.grade_name, source_path=EXCLUDED.source_path,
            owner_id=EXCLUDED.owner_id, status='processing', updated_at=NOW()
        """, (document_id, safe_name, grade_id, GRADE_NAMES[grade_id], source_path, created_by))
        cur.execute("""
          INSERT INTO knowledge_document_versions
            (version_id, document_id, file_name, storage_path, file_hash, status, created_by)
          VALUES (%s,%s,%s,%s,%s,'uploaded',%s)
          ON CONFLICT (version_id) DO NOTHING
        """, (version_id, document_id, safe_name, source_path, digest, created_by))
        cur.execute("""
          INSERT INTO ingestion_jobs (job_id, version_id, status, stage, created_by)
          VALUES (%s,%s,'queued','uploaded',%s)
        """, (job_id, version_id, created_by))
    return {"job_id": job_id, "document_id": document_id, "version_id": version_id, "status": "queued"}


def _claim_job(database_url: str) -> tuple[Any, ...] | None:
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("""
          WITH next_job AS (
            SELECT job_id FROM ingestion_jobs
            WHERE status = 'queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
          )
          UPDATE ingestion_jobs j
          SET status='processing', stage='parsing', started_at=NOW()
          FROM next_job n
          WHERE j.job_id=n.job_id
          RETURNING j.job_id, j.version_id
        """)
        return cur.fetchone()


def _update_job(database_url: str, job_id: str, *, status: str, stage: str,
                progress: int, error_message: str | None = None) -> None:
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("""
          UPDATE ingestion_jobs
          SET status=%s, stage=%s, progress=%s, error_message=%s,
              finished_at=CASE WHEN %s IN ('completed','failed','cancelled') THEN NOW() ELSE finished_at END
          WHERE job_id=%s
        """, (status, stage, progress, error_message, status, job_id))


def process_one(database_url: str | None = None) -> dict[str, str] | None:
    """Process one queued job; safe to run from multiple workers."""
    database_url = database_url or database_url_from_env()
    claimed = _claim_job(database_url)
    if not claimed:
        return None
    job_id, version_id = claimed
    try:
        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute("""
              SELECT v.document_id, v.storage_path, v.file_name, d.grade_id, d.grade_name, d.owner_id
              FROM knowledge_document_versions v
              JOIN knowledge_documents d USING(document_id)
              WHERE v.version_id=%s
            """, (version_id,))
            row = cur.fetchone()
        if not row:
            raise ValueError("找不到文档版本")
        document_id, storage_path, file_name, grade_id, grade_name, owner_id = row
        path = Path(storage_path)
        if not path.exists():
            raise FileNotFoundError(storage_path)
        _update_job(database_url, job_id, status="processing", stage="parsing", progress=20)
        if path.suffix.lower() in {".md", ".markdown", ".txt"}:
            chunks = parse_markdown(path)
        else:
            chunks = parse_question_bank(path)
        for chunk in chunks:
            # The uploader-selected grade is authoritative when a filename has
            # no grade hint; document IDs and source paths remain unchanged.
            chunk.metadata.update({"grade_id": grade_id, "grade": grade_name})
        _update_job(database_url, job_id, status="processing", stage="indexing", progress=55)
        upsert_chunks(database_url, chunks, owner_id=owner_id, status="processing",
                      version_ids={document_id: version_id})
        config = DashScopeConfig.from_env()
        embed_corpus(database_url, model=config.embedding_model, dimensions=config.embedding_dimensions,
                     batch_size=config.embedding_batch_size)
        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute("""UPDATE knowledge_documents
                           SET document_name=%s, status='published', current_version_id=%s,
                               updated_at=NOW()
                           WHERE document_id=%s""",
                        (file_name, version_id, document_id))
            cur.execute("UPDATE knowledge_document_versions SET status='published' WHERE version_id=%s",
                        (version_id,))
        _update_job(database_url, job_id, status="completed", stage="published", progress=100)
        return {"job_id": job_id, "status": "completed"}
    except Exception as exc:
        _update_job(database_url, job_id, status="failed", stage="failed", progress=0,
                    error_message=f"{type(exc).__name__}: {exc}")
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def run_worker(database_url: str | None = None, *, poll_seconds: float = 2.0,
               once: bool = False) -> None:
    database_url = database_url or database_url_from_env()
    while True:
        result = process_one(database_url)
        if result is None:
            if once:
                return
            time.sleep(max(0.2, poll_seconds))
        elif once:
            return
