from __future__ import annotations

import hashlib
import csv
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import psycopg

from .ingestion import parse_markdown, parse_question_bank
from .dashscope import DashScopeConfig
from .pg_knowledge import SCHEMA_SQL, database_url_from_env, embed_corpus, upsert_chunks


GRADE_NAMES = {"grade7": "七年级", "grade8": "八年级", "grade9": "九年级"}
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".jsonl", ".csv"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def upload_document_id(file_name: str, grade_id: str) -> str:
    """Return a stable logical document ID for repeated uploads of one source."""
    raw = f"{grade_id}\0{file_name}".encode("utf-8")
    return "doc_" + hashlib.sha1(raw).hexdigest()[:20]


def validate_upload_payload(file_name: str, payload: bytes) -> str:
    """Validate file type and structure before creating a durable job."""
    safe_name = Path(file_name or "upload.md").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("仅支持 Markdown、TXT、JSON、JSONL 和 CSV 文件")
    if not payload:
        raise ValueError("上传文件不能为空")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("文件不能超过 20 MB")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文件必须使用 UTF-8 编码") from exc
    if not text.strip():
        raise ValueError("上传文件不能是空白文件")
    try:
        if suffix == ".json":
            parsed = json.loads(text)
            rows = parsed if isinstance(parsed, list) else parsed.get("questions", parsed.get("items", [])) if isinstance(parsed, dict) else None
            if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
                raise ValueError("JSON 题库必须包含非空的对象记录列表")
        elif suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            if not rows or not all(isinstance(row, dict) for row in rows):
                raise ValueError("JSONL 题库必须每行包含一个对象记录")
        elif suffix == ".csv":
            rows = list(csv.DictReader(text.splitlines()))
            if not rows or not any(any(str(value or "").strip() for value in row.values()) for row in rows):
                raise ValueError("CSV 题库必须包含表头和至少一条记录")
        elif not text.strip():
            raise ValueError("文本内容不能为空")
    except json.JSONDecodeError as exc:
        raise ValueError("题库文件不是有效的 JSON 格式") from exc
    except csv.Error as exc:
        raise ValueError("题库文件不是有效的 CSV 格式") from exc
    return safe_name


def enqueue_upload(database_url: str, file_name: str, payload: bytes, grade_id: str,
                   created_by: str, upload_root: str | Path = "data/uploads") -> dict[str, str]:
    """Persist an uploaded file and enqueue durable background processing."""
    if grade_id not in GRADE_NAMES:
        raise ValueError("grade_id 必须是 grade7、grade8 或 grade9")
    safe_name = validate_upload_payload(file_name, payload)
    digest = hashlib.sha256(payload).hexdigest()
    job_id = "job_" + secrets.token_urlsafe(12)
    root = Path(upload_root)
    root.mkdir(parents=True, exist_ok=True)
    storage_path = root / f"{job_id}_{safe_name}"
    storage_path.write_bytes(payload)
    document_id = upload_document_id(safe_name, grade_id)
    version_id = "ver_" + hashlib.sha1(f"{document_id}\0{digest}".encode("utf-8")).hexdigest()[:20]
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
        if status == "processing":
            cur.execute("""
              UPDATE knowledge_document_versions v
              SET status='processing'
              FROM ingestion_jobs j
              WHERE j.job_id=%s AND v.version_id=j.version_id
            """, (job_id,))
        elif status == "failed":
            cur.execute("""
              UPDATE knowledge_document_versions v
              SET status='failed'
              FROM ingestion_jobs j
              WHERE j.job_id=%s AND v.version_id=j.version_id
            """, (job_id,))
            cur.execute("""
              UPDATE knowledge_documents d
              SET status='failed', updated_at=NOW()
              FROM ingestion_jobs j
              JOIN knowledge_document_versions v USING (version_id)
              WHERE j.job_id=%s AND d.document_id=v.document_id
            """, (job_id,))


def retry_document_job(database_url: str, job_id: str) -> dict[str, str | int]:
    """Requeue one failed import job without creating a duplicate job."""
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("""
          SELECT j.status, j.version_id, v.document_id, v.storage_path
          FROM ingestion_jobs j
          JOIN knowledge_document_versions v USING (version_id)
          WHERE j.job_id=%s
          FOR UPDATE
        """, (job_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError(job_id)
        status, version_id, document_id, storage_path = row
        if status != "failed":
            raise ValueError("只有失败的导入任务可以重新处理")
        if not Path(storage_path).exists():
            raise ValueError("原始文件不存在，无法重新处理")
        cur.execute("""
          UPDATE ingestion_jobs
          SET status='queued', stage='retry_queued', progress=0,
              retry_count=retry_count+1, error_message=NULL,
              started_at=NULL, finished_at=NULL
          WHERE job_id=%s AND status='failed'
          RETURNING job_id, retry_count
        """, (job_id,))
        updated = cur.fetchone()
        if not updated:
            raise ValueError("任务状态已变化，请刷新后再试")
        cur.execute("""
          UPDATE knowledge_document_versions
          SET status='uploaded'
          WHERE version_id=%s
        """, (version_id,))
        cur.execute("""
          UPDATE knowledge_documents
          SET status='processing', updated_at=NOW()
          WHERE document_id=%s
        """, (document_id,))
    return {"job_id": updated[0], "status": "queued", "retry_count": updated[1]}


def recover_stale_jobs(database_url: str, timeout_seconds: int = 1800) -> int:
    """Fail processing jobs that have exceeded the worker lease window."""
    if timeout_seconds <= 0:
        raise ValueError("任务超时时间必须大于 0")
    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute("""
          UPDATE ingestion_jobs
          SET status='failed', stage='timeout', progress=0,
              error_message=%s, finished_at=NOW()
          WHERE status='processing'
            AND started_at IS NOT NULL
            AND started_at < NOW() - (%s * INTERVAL '1 second')
          RETURNING job_id
        """, (f"任务超过 {timeout_seconds} 秒未完成", timeout_seconds))
        job_ids = [row[0] for row in cur.fetchall()]
        for job_id in job_ids:
            cur.execute("""
              UPDATE knowledge_document_versions v
              SET status='failed'
              FROM ingestion_jobs j
              WHERE j.job_id=%s AND v.version_id=j.version_id
            """, (job_id,))
            cur.execute("""
              UPDATE knowledge_documents d
              SET status='failed', updated_at=NOW()
              FROM ingestion_jobs j
              JOIN knowledge_document_versions v USING (version_id)
              WHERE j.job_id=%s AND d.document_id=v.document_id
            """, (job_id,))
    return len(job_ids)


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
    timeout_seconds = int(os.getenv("INGESTION_JOB_TIMEOUT_SECONDS", "1800"))
    while True:
        recover_stale_jobs(database_url, timeout_seconds)
        result = process_one(database_url)
        if result is None:
            if once:
                return
            time.sleep(max(0.2, poll_seconds))
        elif once:
            return
