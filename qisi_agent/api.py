from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from typing import Literal

from .agents import CheckpointStore, Supervisor
from .auth import AuthError, AuthStore, User
from .memory import MemoryStore
from .practice import PracticeService

try:  # Optional dependency: core retrieval remains usable without FastAPI.
    from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Header, Request
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover
    FastAPI = None


if FastAPI is not None:
    class ChatRequest(BaseModel):
        query: str = Field(min_length=1, description="学生的问题")
        user_id: str = Field(default="anonymous", min_length=1)
        session_id: str = Field(default="default", min_length=1)
        course_id: str | None = Field(default=None, description="课程过滤条件")
        grade_id: str | None = Field(default=None, description="年级过滤条件，如 grade7")


    class MemoryReviewRequest(BaseModel):
        action: str = Field(description="approve、activate、correct 或 delete")
        content: str | None = Field(default=None, description="correct 时的新内容")


    class AuthRequest(BaseModel):
        username: str = Field(min_length=3, max_length=64)
        password: str = Field(min_length=6, max_length=128)
        display_name: str = Field(default="", max_length=80)
        role: Literal["student", "teacher", "admin"] = "student"
        remember_me: bool = False


    class PasswordChangeRequest(BaseModel):
        current_password: str = Field(min_length=6, max_length=128)
        new_password: str = Field(min_length=6, max_length=128)


    class AdminRegisterRequest(BaseModel):
        username: str = Field(min_length=3, max_length=64)
        password: str = Field(min_length=6, max_length=128)
        display_name: str = Field(default="", max_length=80)
        application_code: str = Field(min_length=1, max_length=64)


    class AdminUserUpdateRequest(BaseModel):
        role: str | None = None
        status: str | None = None


    class PracticeSubmitRequest(BaseModel):
        quiz_id: str = Field(min_length=1, max_length=128)
        answers: dict[str, int] = Field(default_factory=dict)


def create_app(rag: Any, *, memory: Any | None = None,
               checkpointer: Any | None = None,
               auth: Any | None = None,
               practice: Any | None = None):
    if FastAPI is None:
        raise RuntimeError("启动 API 需要安装可选依赖：pip install -e '.[api]'")
    app = FastAPI(title="启思学伴 Agent", version="0.1.0")
    auth = auth or AuthStore()
    bearer = HTTPBearer(auto_error=False)
    supervisor = Supervisor(rag, memory=memory, checkpointer=checkpointer)
    practice = practice or PracticeService(rag.index.chunks)
    login_failures: dict[tuple[str, str], tuple[int, float]] = {}
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    def frontend_document():
        return FileResponse(
            frontend_dir / "index.html",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get("/", include_in_schema=False)
    def frontend():
        return frontend_document()

    # The frontend owns navigation below these role prefixes.  Returning the
    # same application document lets a refresh or a shared deep link preserve
    # the current screen instead of falling through to a 404 response.
    @app.get("/student/{path:path}", include_in_schema=False)
    def student_frontend(path: str):
        del path
        return frontend_document()

    @app.get("/teacher/{path:path}", include_in_schema=False)
    def teacher_frontend(path: str):
        del path
        return frontend_document()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="请先登录", headers={"WWW-Authenticate": "Bearer"})
        try:
            return auth.user_from_token(credentials.credentials)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    def require_teacher(user: User = Depends(current_user)) -> User:
        if user.role not in {"teacher", "admin"}:
            raise HTTPException(status_code=403, detail="教师权限不足")
        return user

    def require_learning_user(user: User = Depends(current_user)) -> User:
        if user.role == "admin":
            raise HTTPException(status_code=403, detail="管理员请使用管理后台")
        return user

    def auth_response(user: User, *, remember_me: bool = False) -> dict:
        # Only students may opt into a browser-persistent login.  The server
        # independently enforces short administrator/teacher token lifetimes.
        persistent = user.role == "student" and remember_me
        return {"user": user.to_dict(), "access_token": auth.issue_token(user, remember_me=persistent),
                "token_type": "bearer", "persistent_login": persistent}

    def login_key(request: Request, username: str) -> tuple[str, str]:
        host = request.client.host if request.client else "unknown"
        return host, username.strip().lower()

    def assert_login_allowed(key: tuple[str, str]) -> None:
        failures, blocked_until = login_failures.get(key, (0, 0.0))
        if blocked_until > time.monotonic():
            raise HTTPException(status_code=429, detail="尝试次数过多，请 15 分钟后再试")
        if blocked_until:
            login_failures.pop(key, None)

    def record_login_failure(key: tuple[str, str]) -> None:
        failures, blocked_until = login_failures.get(key, (0, 0.0))
        failures += 1
        login_failures[key] = (failures, time.monotonic() + 15 * 60 if failures >= 5 else blocked_until)

    @app.post("/api/auth/register")
    def register(payload: AuthRequest):
        try:
            user = auth.register(payload.username, payload.password, payload.display_name, payload.role)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return auth_response(user)

    @app.post("/api/auth/admin-register")
    def admin_register(payload: AdminRegisterRequest):
        try:
            user = auth.register_admin(
                payload.username, payload.password, payload.display_name,
                payload.application_code, os.getenv("ADMIN_REGISTRATION_CODE", ""),
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return auth_response(user)

    @app.post("/api/auth/login")
    def login(payload: AuthRequest, request: Request):
        key = login_key(request, payload.username)
        assert_login_allowed(key)
        try:
            user = auth.authenticate(payload.username, payload.password, payload.role)
        except AuthError as exc:
            record_login_failure(key)
            raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
        login_failures.pop(key, None)
        return auth_response(user, remember_me=payload.remember_me)

    @app.get("/api/auth/me")
    def me(user: User = Depends(current_user)):
        return user.to_dict()

    @app.post("/api/auth/logout-all")
    def logout_all(user: User = Depends(current_user)):
        auth.revoke_all_tokens(user.user_id)
        return {"status": "ok"}

    @app.post("/api/auth/change-password")
    def change_password(payload: PasswordChangeRequest, user: User = Depends(current_user)):
        try:
            auth.change_password(user.user_id, payload.current_password, payload.new_password)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "message": "密码已更新，请重新登录"}

    @app.get("/admin", include_in_schema=False)
    def admin_frontend():
        return frontend_document()

    @app.get("/admin/login", include_in_schema=False)
    def admin_login_frontend():
        return frontend_document()

    @app.get("/admin/{path:path}", include_in_schema=False)
    def admin_app_frontend(path: str):
        del path
        return frontend_document()

    @app.get("/api/admin/overview")
    def admin_overview(user: User = Depends(require_teacher)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="管理员权限不足")
        activity = auth.admin_activity_counts()
        user_counts = auth.admin_user_counts()
        return {"user_count": user_counts["user_count"], "active_users": user_counts["active_users"],
                "disabled_users": user_counts["disabled_users"], "knowledge_chunks": len(rag.index.chunks),
                "embedding_model": rag.index.embedding_model,
                **activity, **user_counts}

    @app.get("/api/admin/users")
    def admin_users(search: str = "", limit: int = 50, offset: int = 0,
                    user: User = Depends(require_teacher)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="管理员权限不足")
        items, total = auth.list_users(search, limit, offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/admin/questions")
    def admin_questions(search: str = "", user_id: str = "", limit: int = 50, offset: int = 0,
                        user: User = Depends(require_teacher)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="管理员权限不足")
        items, total = auth.list_admin_questions(search, user_id, limit, offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/admin/mistakes")
    def admin_mistakes(search: str = "", status: str = "all", user_id: str = "",
                       knowledge_point_id: str = "",
                       limit: int = 50, offset: int = 0,
                       user: User = Depends(require_teacher)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="管理员权限不足")
        if status not in {"all", "unreviewed", "reviewed"}:
            raise HTTPException(status_code=400, detail="错题状态不正确")
        items, total = auth.list_admin_mistakes(search, status, user_id, knowledge_point_id, limit, offset)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/admin/activity-options")
    def admin_activity_options(user: User = Depends(require_teacher)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="管理员权限不足")
        return auth.admin_activity_options()

    @app.post("/api/documents/upload")
    async def upload_document(request: Request, grade_id: str = "grade7",
                              x_filename: str = Header(default="upload.md"),
                              user: User = Depends(require_teacher)):
        """Save an upload and enqueue durable background processing.

        The request body is the raw file bytes; the client supplies the original
        name in X-Filename and the selected grade as a query parameter. This
        avoids tying the API to a particular multipart implementation.
        """
        payload = await request.body()
        if len(payload) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件不能超过 20 MB")
        try:
            from .async_ingestion import enqueue_upload
            from .pg_knowledge import database_url_from_env
            return enqueue_upload(database_url_from_env(), x_filename, payload, grade_id, user.user_id)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/documents")
    def documents(limit: int = 50, offset: int = 0, user: User = Depends(require_teacher)):
        del user
        from .pg_knowledge import database_url_from_env
        import psycopg
        limit = max(1, min(limit, 100)); offset = max(0, offset)
        with psycopg.connect(database_url_from_env()) as conn, conn.cursor() as cur:
            cur.execute("""SELECT document_id, document_name, grade_id, grade_name, status,
                          owner_id, source_path, document_type, current_version_id,
                          (SELECT COUNT(*) FROM knowledge_document_versions v
                           WHERE v.document_id = d.document_id) AS version_count,
                          updated_at FROM knowledge_documents d
                          ORDER BY updated_at DESC LIMIT %s OFFSET %s""", (limit, offset))
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM knowledge_documents")
            total = cur.fetchone()[0]
        keys = ("document_id", "document_name", "grade_id", "grade_name", "status",
                "owner_id", "source_path", "document_type", "current_version_id",
                "version_count", "updated_at")
        return {"items": [dict(zip(keys, row)) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    @app.get("/api/documents/jobs")
    def document_jobs(limit: int = 50, offset: int = 0,
                      user: User = Depends(require_teacher)):
        """List recent durable document import jobs for the content workbench."""
        del user
        from .pg_knowledge import database_url_from_env
        import psycopg
        limit = max(1, min(limit, 100)); offset = max(0, offset)
        with psycopg.connect(database_url_from_env()) as conn, conn.cursor() as cur:
            cur.execute("""SELECT job_id, status, stage, progress, retry_count,
                          error_message, created_by, created_at, started_at, finished_at,
                          version_id, document_id, file_name, document_name,
                          document_type, grade_id, grade_name
                          FROM admin_content_imports
                          ORDER BY created_at DESC LIMIT %s OFFSET %s""", (limit, offset))
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM admin_content_imports")
            total = cur.fetchone()[0]
        keys = ("job_id", "status", "stage", "progress", "retry_count", "error_message",
                "created_by", "created_at", "started_at", "finished_at", "version_id",
                "document_id", "file_name", "document_name", "document_type", "grade_id",
                "grade_name")
        return {"items": [dict(zip(keys, row)) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    @app.get("/api/documents/jobs/{job_id}")
    def document_job(job_id: str, user: User = Depends(current_user)):
        from .pg_knowledge import database_url_from_env
        import psycopg
        with psycopg.connect(database_url_from_env()) as conn, conn.cursor() as cur:
            cur.execute("""
              SELECT j.job_id, j.status, j.stage, j.progress, j.error_message,
                     j.created_by, j.created_at, j.started_at, j.finished_at,
                     v.document_id, v.version_id, v.file_name
              FROM ingestion_jobs j
              JOIN knowledge_document_versions v USING(version_id)
              WHERE j.job_id=%s
            """, (job_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="导入任务不存在")
        if user.role not in {"teacher", "admin"} and row[5] != user.user_id:
            raise HTTPException(status_code=403, detail="无权查看该导入任务")
        keys = ("job_id", "status", "stage", "progress", "error_message", "created_by",
                "created_at", "started_at", "finished_at", "document_id", "version_id", "file_name")
        return dict(zip(keys, row))

    @app.post("/api/documents/jobs/{job_id}/retry")
    def retry_document_job(job_id: str, user: User = Depends(require_teacher)):
        from .async_ingestion import retry_document_job as requeue_job
        from .pg_knowledge import database_url_from_env
        try:
            return requeue_job(database_url_from_env(), job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="导入任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/documents/{document_id}/versions/{version_id}/preview")
    def document_version_preview(document_id: str, version_id: str,
                                 limit: int = 50, offset: int = 0,
                                 user: User = Depends(require_teacher)):
        del user
        from .pg_knowledge import database_url_from_env
        import psycopg
        limit = max(1, min(limit, 100)); offset = max(0, offset)
        with psycopg.connect(database_url_from_env()) as conn, conn.cursor() as cur:
            cur.execute("""
              SELECT d.document_id, d.document_name, d.grade_id, d.grade_name,
                     d.document_type, d.status, d.current_version_id,
                     v.version_id, v.file_name, v.file_hash, v.status,
                     v.created_by, v.created_at
              FROM knowledge_documents d
              JOIN knowledge_document_versions v ON v.document_id=d.document_id
              WHERE d.document_id=%s AND v.version_id=%s
            """, (document_id, version_id))
            version = cur.fetchone()
            if not version:
                raise HTTPException(status_code=404, detail="文档版本不存在")
            cur.execute("""
              SELECT content_id, chapter_id, title, content, metadata,
                     source_chunk_id, created_at, updated_at
              FROM knowledge_contents
              WHERE document_id=%s AND version_id=%s
              ORDER BY chapter_id, title, content_id
              LIMIT %s OFFSET %s
            """, (document_id, version_id, limit, offset))
            contents = cur.fetchall()
            cur.execute("""
              SELECT COUNT(*)
              FROM knowledge_contents
              WHERE document_id=%s AND version_id=%s
            """, (document_id, version_id))
            total = cur.fetchone()[0]
        version_keys = ("document_id", "document_name", "grade_id", "grade_name",
                        "document_type", "document_status", "current_version_id",
                        "version_id", "file_name", "file_hash", "version_status",
                        "created_by", "created_at")
        content_keys = ("content_id", "chapter_id", "title", "content", "metadata",
                        "source_chunk_id", "created_at", "updated_at")
        return {"document": dict(zip(version_keys[:7], version[:7])),
                "version": dict(zip(version_keys[7:], version[7:])),
                "items": [dict(zip(content_keys, row)) for row in contents],
                "total": total, "limit": limit, "offset": offset}

    @app.get("/api/documents/{document_id}/versions")
    def document_versions(document_id: str, user: User = Depends(require_teacher)):
        del user
        from .pg_knowledge import database_url_from_env
        import psycopg
        with psycopg.connect(database_url_from_env()) as conn, conn.cursor() as cur:
            cur.execute("""
              SELECT d.document_id, d.document_name, d.current_version_id,
                     v.version_id, v.file_name, v.file_hash, v.status,
                     v.created_by, v.created_at,
                     COUNT(c.content_id) AS content_count
              FROM knowledge_documents d
              JOIN knowledge_document_versions v ON v.document_id=d.document_id
              LEFT JOIN knowledge_contents c
                ON c.document_id=v.document_id AND c.version_id=v.version_id
              WHERE d.document_id=%s
              GROUP BY d.document_id, d.document_name, d.current_version_id,
                       v.version_id, v.file_name, v.file_hash, v.status,
                       v.created_by, v.created_at
              ORDER BY v.created_at DESC
            """, (document_id,))
            rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="文档不存在或没有版本")
        keys = ("document_id", "document_name", "current_version_id", "version_id",
                "file_name", "file_hash", "status", "created_by", "created_at",
                "content_count")
        return {"document_id": document_id, "items": [
            {**dict(zip(keys, row)), "is_current": row[2] == row[3]}
            for row in rows
        ]}

    @app.post("/api/documents/{document_id}/versions/{version_id}/publish")
    def publish_document_version(document_id: str, version_id: str,
                                 user: User = Depends(require_teacher)):
        del user
        from .pg_knowledge import database_url_from_env
        import psycopg
        with psycopg.connect(database_url_from_env()) as conn, conn.cursor() as cur:
            cur.execute("""
              SELECT v.status, d.current_version_id, COUNT(c.content_id)
              FROM knowledge_document_versions v
              JOIN knowledge_documents d USING(document_id)
              LEFT JOIN knowledge_contents c
                ON c.document_id=v.document_id AND c.version_id=v.version_id
              WHERE v.document_id=%s AND v.version_id=%s
              GROUP BY v.status, d.current_version_id
            """, (document_id, version_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="文档版本不存在")
            status, current_version_id, content_count = row
            if status in {"failed", "uploaded", "processing"} or not content_count:
                raise HTTPException(status_code=409, detail="该版本尚未完成解析，不能设为当前版本")
            cur.execute("""
              UPDATE knowledge_document_versions
              SET status='archived'
              WHERE document_id=%s AND status='published' AND version_id<>%s
            """, (document_id, version_id))
            cur.execute("""
              UPDATE knowledge_document_versions
              SET status='published'
              WHERE document_id=%s AND version_id=%s
            """, (document_id, version_id))
            cur.execute("""
              UPDATE knowledge_documents
              SET status='published', current_version_id=%s, updated_at=NOW()
              WHERE document_id=%s
            """, (version_id, document_id))
        return {"document_id": document_id, "version_id": version_id,
                "previous_version_id": current_version_id, "status": "published"}

    @app.patch("/api/admin/users/{user_id}")
    def admin_update_user(user_id: str, payload: AdminUserUpdateRequest,
                          user: User = Depends(require_teacher)):
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="管理员权限不足")
        if user.user_id == user_id and (payload.role != "admin" or payload.status == "disabled"):
            raise HTTPException(status_code=400, detail="不能降级或禁用当前管理员账号")
        try:
            updated = auth.update_user(user_id, role=payload.role, status=payload.status)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return updated.to_dict()

    @app.post("/api/chat/stream")
    def chat_stream(payload: ChatRequest, background_tasks: BackgroundTasks,
                    user: User = Depends(require_learning_user)):
        try:
            auth.claim_session(payload.session_id, user.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        def events():
            stream = supervisor.run_stream(user.user_id, payload.session_id, payload.query,
                                           course_id=payload.course_id, grade_id=payload.grade_id)
            for item in stream:
                if item["type"] == "progress":
                    yield _sse("progress", {"stage": item["stage"], "message": item["message"]})
                elif item["type"] == "meta":
                    prepared = item["prepared"]
                    for citation in prepared["citations"]:
                        yield _sse("citation", citation.to_dict())
                elif item["type"] == "delta":
                    yield _sse("answer_delta", {"delta": item["delta"]})
                elif item["type"] == "error":
                    yield _sse("error", {"code": "model_error", "message": item["message"]})
                elif item["type"] == "done":
                    yield _sse("done", {"confidence": item["confidence"], "refused": item.get("refused", False),
                                         "source": item["source"]})
                elif item["type"] == "memory_update":
                    background_tasks.add_task(
                        supervisor.persist_completed_turn, item["user_id"], item["session_id"],
                        item["query"], item["answer"],
                    )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/practice/quiz")
    def practice_quiz(grade_id: str = "grade7", count: int = 5, difficulty: str | None = None,
                      knowledge_point: str | None = None,
                      user: User = Depends(require_learning_user)):
        try:
            quiz = practice.create_quiz(user.user_id, grade_id, count, difficulty, knowledge_point)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"quiz_id": quiz.quiz_id, "grade_id": quiz.grade_id,
                "questions": [question.to_public_dict() for question in quiz.questions]}

    @app.get("/api/practice/categories")
    def practice_categories(grade_id: str = "grade7", user: User = Depends(require_learning_user)):
        del user
        if grade_id not in {"grade7", "grade8", "grade9"}:
            raise HTTPException(status_code=422, detail="grade_id 必须是 grade7、grade8 或 grade9")
        if hasattr(practice, "list_categories"):
            return {"grade_id": grade_id, "items": practice.list_categories(grade_id)}
        items = []
        seen = set()
        for chunk in getattr(practice, "chunks", []):
            if chunk.metadata.get("grade_id") != grade_id:
                continue
            chapter = chunk.chapter_id or "未分类"
            for point in chunk.knowledge_point_ids or [chunk.title]:
                if (chapter, point) not in seen:
                    seen.add((chapter, point)); items.append({"chapter": chapter, "knowledge_point": point})
        return {"grade_id": grade_id, "items": sorted(items, key=lambda item: (item["chapter"], item["knowledge_point"]))}

    @app.post("/api/practice/submit")
    def practice_submit(payload: PracticeSubmitRequest,
                        user: User = Depends(require_learning_user)):
        try:
            result = practice.submit(payload.quiz_id, user.user_id, payload.answers)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="练习不存在或不属于当前用户") from exc
        for item in result["results"]:
            supervisor.memory.record_practice_result(user.user_id, item["knowledge_point"], item["is_correct"])
            if not item["is_correct"]:
                supervisor.memory.write_mistake(user.user_id, prompt=item["citation"]["title"],
                                                correct_answer=item["citation"]["excerpt"],
                                                explanation=item["explanation"],
                                                knowledge_point_id=item["knowledge_point"],
                                                source_message_id=payload.quiz_id)
                supervisor.memory.write(user.user_id, f"练习错题：{item['knowledge_point']}",
                                        knowledge_point_id=item["knowledge_point"], source_message_id=payload.quiz_id)
        return result

    @app.get("/api/students/{student_id}/mistakes")
    def student_mistakes(student_id: str, user: User = Depends(require_learning_user)):
        if user.role == "student" and student_id != user.user_id:
            raise HTTPException(status_code=403, detail="只能查看自己的错题")
        return {"student_id": student_id, "items": supervisor.memory.list_mistakes(student_id)}

    @app.post("/api/mistakes/{mistake_id}/review")
    def review_mistake(mistake_id: str, user: User = Depends(require_learning_user)):
        try:
            return supervisor.memory.review_mistake(mistake_id, user.user_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="错题不存在") from exc

    @app.get("/api/retrieval/debug")
    def retrieval_debug(query: str, top_k: int = 5, grade_id: str | None = None,
                       user: User = Depends(require_teacher)):
        return JSONResponse({"query": query, "hits": [hit.to_dict() for hit in rag.index.search(
            query, top_k, grade_id=grade_id)]})

    @app.get("/api/conversations/{session_id}")
    def conversation(session_id: str, user: User = Depends(require_learning_user)):
        if not auth.session_exists(session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        if not auth.owns_session(session_id, user.user_id):
            raise HTTPException(status_code=403, detail="无权访问该会话")
        return supervisor.conversation(session_id)

    @app.get("/api/conversations")
    def conversations(user: User = Depends(require_learning_user)):
        return {"items": [supervisor.memory.session_summary(session_id)
                           for session_id in auth.list_sessions(user.user_id)]}

    @app.post("/api/conversations")
    def create_conversation(user: User = Depends(require_learning_user)):
        session_id = f"session-{user.user_id}-{os.urandom(8).hex()}"
        auth.claim_session(session_id, user.user_id)
        return supervisor.memory.session_summary(session_id)

    @app.delete("/api/conversations/{session_id}")
    def delete_conversation(session_id: str, user: User = Depends(require_learning_user)):
        if not auth.delete_session(session_id, user.user_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        supervisor.memory.delete_session(session_id)
        if hasattr(supervisor.checkpointer, "_states"):
            supervisor.checkpointer._states.pop(session_id, None)
        elif hasattr(supervisor.checkpointer, "delete"):
            supervisor.checkpointer.delete(session_id)
        return {"deleted": True, "session_id": session_id}

    @app.get("/api/students/{student_id}/learning-profile")
    def learning_profile(student_id: str, user: User = Depends(require_learning_user)):
        if user.role == "student" and student_id != user.user_id:
            raise HTTPException(status_code=403, detail="只能查看自己的学习画像")
        return supervisor.memory.profile(student_id)

    @app.get("/api/students/{student_id}/memories")
    def student_memories(student_id: str, include_deleted: bool = False,
                         user: User = Depends(require_learning_user)):
        if user.role == "student" and student_id != user.user_id:
            raise HTTPException(status_code=403, detail="只能查看自己的学习记忆")
        return {"student_id": student_id,
                "items": [item.to_dict() for item in supervisor.memory.list_for_student(
                    student_id, include_deleted=include_deleted)]}

    @app.get("/api/teacher/overview")
    def teacher_overview(user: User = Depends(require_teacher)):
        """聚合教师首屏需要的轻量学情摘要；正式环境应叠加角色鉴权。"""
        # Aggregate directly over the store because this endpoint spans students.
        all_items = list(supervisor.memory.long_term.values())
        visible = [item for item in all_items if item.status != "deleted"]
        students: dict[str, list] = {}
        for item in visible:
            students.setdefault(item.student_id, []).append(item)
        student_rows = []
        for student_id, records in sorted(students.items()):
            points = sorted({item.knowledge_point_id for item in records if item.knowledge_point_id})
            pending = next((item for item in records if item.status == "candidate"), None)
            student_rows.append({
                "student_id": student_id,
                "memory_count": len(records),
                "pending_review": sum(item.status == "candidate" for item in records),
                "pending_memory_id": pending.memory_id if pending else None,
                "knowledge_points": points,
                "recent_event": records[0].content,
            })
        return {
            "student_count": len(student_rows),
            "memory_count": len(visible),
            "pending_review": sum(item.status == "candidate" for item in visible),
            "knowledge_points": sorted({item.knowledge_point_id for item in visible if item.knowledge_point_id}),
            "students": student_rows,
        }

    @app.get("/api/teacher/students/{student_id}")
    def teacher_student_detail(student_id: str, user: User = Depends(require_teacher)):
        profile = supervisor.memory.profile(student_id)
        mistakes = supervisor.memory.list_mistakes(student_id)
        sessions = [supervisor.conversation(session_id) for session_id in auth.list_sessions(student_id)]
        return {"student_id": student_id, "profile": profile, "mistakes": mistakes,
                "sessions": [{"session_id": item["session_id"], "messages": item["messages"][-6:]}
                             for item in sessions]}

    @app.post("/api/memories/{memory_id}/review")
    def review_memory(memory_id: str, payload: MemoryReviewRequest,
                      user: User = Depends(require_teacher)):
        try:
            item = supervisor.memory.review(memory_id, payload.action, payload.content)
        except KeyError:
            raise HTTPException(status_code=404, detail="memory 不存在")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return item.to_dict()

    return app


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
