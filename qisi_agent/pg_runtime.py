"""PostgreSQL-backed runtime stores and migration helpers."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg

from .auth import AuthError, AuthStore, User, _b64, _unb64
from .embeddings import HashEmbedding, cosine
from .memory import MemoryItem, MemoryStore
from .models import Chunk, utc_now
from .practice import PracticeQuestion, PracticeQuiz, PracticeService
from .question_bank import ensure_bank_table


def ensure_runtime_schema(database_url: str) -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    sql = (migrations_dir / "001_runtime_schema.sql").read_text(encoding="utf-8")
    admin_activity_sql = (migrations_dir / "002_admin_activity.sql").read_text(encoding="utf-8")
    admin_scope_sql = (migrations_dir / "003_admin_directory_scope.sql").read_text(encoding="utf-8")
    content_lineage_sql = (migrations_dir / "004_knowledge_content_lineage.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url) as conn:
        conn.execute(sql)
        conn.execute("INSERT INTO runtime_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING", ("001_runtime_schema",))
        # The knowledge tables are created by pg_knowledge.SCHEMA_SQL during
        # import/startup.  The additive lineage migration is safe to run
        # before or after that bootstrap and makes the relationship explicit.
        has_knowledge_schema = conn.execute(
            "SELECT to_regclass('public.knowledge_documents')"
        ).fetchone()[0]
        if has_knowledge_schema:
            conn.execute(content_lineage_sql)
            conn.execute("INSERT INTO runtime_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING", ("004_knowledge_content_lineage",))
        conn.execute(admin_activity_sql)
        conn.execute("INSERT INTO runtime_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING", ("002_admin_activity",))
        conn.execute(admin_scope_sql)
        conn.execute("INSERT INTO runtime_schema_migrations(version) VALUES (%s) ON CONFLICT DO NOTHING", ("003_admin_directory_scope",))


class PostgresAuthStore:
    def __init__(self, database_url: str, secret_key: str | None = None, token_ttl_seconds: int = 86400):
        self.database_url, self.token_ttl_seconds = database_url, token_ttl_seconds
        ensure_runtime_schema(database_url)
        if secret_key:
            self.secret = secret_key.encode()
        else:
            with psycopg.connect(database_url) as conn:
                row = conn.execute("SELECT setting_value FROM runtime_settings WHERE setting_key='auth_secret'").fetchone()
                if row:
                    self.secret = row[0].encode()
                else:
                    self.secret = secrets.token_urlsafe(32).encode()
                    conn.execute("INSERT INTO runtime_settings(setting_key,setting_value) VALUES ('auth_secret',%s)", (self.secret.decode(),))

    def _validate(self, username: str, password: str) -> tuple[str, str]:
        return AuthStore._validate_credentials(username, password)

    def register(self, username: str, password: str, display_name: str = "", role: str = "student") -> User:
        username, password = self._validate(username, password)
        if role not in {"student", "teacher"}:
            raise AuthError("公开注册只能选择学生或教师，管理员账号请由管理员创建")
        display_name = display_name.strip() or username
        if len(display_name) > 80:
            raise AuthError("显示名称不能超过 80 个字符")
        user_id = secrets.token_urlsafe(12)
        try:
            with psycopg.connect(self.database_url) as conn:
                conn.execute("INSERT INTO app_users(user_id,username,display_name,password_hash,role) VALUES (%s,%s,%s,%s,%s)",
                             (user_id, username, display_name, AuthStore._hash_password(password), role))
        except psycopg.errors.UniqueViolation as exc:
            raise AuthError("用户名已存在") from exc
        return User(user_id, username, display_name, role)

    def register_admin(self, username: str, password: str, display_name: str = "", application_code: str = "", expected_code: str | None = None) -> User:
        if not expected_code or application_code.strip() != expected_code:
            raise AuthError("管理员申请码不正确")
        username, password = self._validate(username, password)
        display_name = display_name.strip() or username
        if len(display_name) > 80:
            raise AuthError("显示名称不能超过 80 个字符")
        user_id = secrets.token_urlsafe(12)
        try:
            with psycopg.connect(self.database_url) as conn:
                conn.execute("INSERT INTO app_users(user_id,username,display_name,password_hash,role) VALUES (%s,%s,%s,%s,'admin')",
                             (user_id, username, display_name, AuthStore._hash_password(password)))
        except psycopg.errors.UniqueViolation as exc:
            raise AuthError("用户名已存在") from exc
        return User(user_id, username, display_name, "admin")

    def _row_user(self, row) -> User:
        return User(row[0], row[1], row[2], row[3])

    def authenticate(self, username: str, password: str, role: str | None = None) -> User:
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute("SELECT user_id,username,display_name,role,password_hash,status FROM app_users WHERE LOWER(username)=LOWER(%s)", (username.strip(),)).fetchone()
        if not row or row[5] == "disabled" or (role is not None and row[3] != role) or not AuthStore._verify_password(password, row[4]):
            if row and row[5] == "disabled":
                raise AuthError("账号已停用，请联系管理员")
            raise AuthError("用户名或密码错误")
        return self._row_user(row)

    def issue_token(self, user: User) -> str:
        now = int(time.time())
        header = _b64(b'{"alg":"HS256","typ":"JWT"}')
        payload = _b64(json.dumps({"sub": user.user_id, "iat": now, "exp": now + self.token_ttl_seconds}, separators=(",", ":")).encode())
        unsigned = f"{header}.{payload}"
        import hmac
        return f"{unsigned}.{_b64(hmac.new(self.secret, unsigned.encode(), hashlib.sha256).digest())}"

    def user_from_token(self, token: str) -> User:
        try:
            header, payload, signature = token.split(".", 2)
            import hmac
            unsigned = f"{header}.{payload}"
            if not hmac.compare_digest(signature, _b64(hmac.new(self.secret, unsigned.encode(), hashlib.sha256).digest())):
                raise AuthError("访问令牌无效")
            data = json.loads(_unb64(payload))
            if int(data["exp"]) < int(time.time()):
                raise AuthError("访问令牌已过期，请重新登录")
            user_id = str(data["sub"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AuthError("访问令牌无效") from exc
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute("SELECT user_id,username,display_name,role,status FROM app_users WHERE user_id=%s", (user_id,)).fetchone()
        if not row or row[4] != "active":
            raise AuthError("用户不存在")
        return self._row_user(row)

    def list_users(self, search: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        limit, offset, pattern = max(1, min(limit, 100)), max(0, offset), f"%{search.strip()}%"
        with psycopg.connect(self.database_url) as conn:
            total = conn.execute("SELECT COUNT(*) FROM admin_user_directory WHERE username ILIKE %s OR display_name ILIKE %s", (pattern, pattern)).fetchone()[0]
            rows = conn.execute("SELECT user_id,username,display_name,role,status,created_at FROM admin_user_directory WHERE username ILIKE %s OR display_name ILIKE %s ORDER BY created_at DESC LIMIT %s OFFSET %s", (pattern, pattern, limit, offset)).fetchall()
        keys = ("user_id", "username", "display_name", "role", "status", "created_at")
        return [dict(zip(keys, row)) for row in rows], total

    def list_admin_questions(self, search: str = "", user_id: str = "",
                             limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """List user-authored questions for the admin activity page."""
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        search_pattern, user_filter = f"%{search.strip()}%", user_id.strip()
        where = ["(username ILIKE %s OR display_name ILIKE %s OR question ILIKE %s)"]
        params: list[Any] = [search_pattern, search_pattern, search_pattern]
        if user_filter:
            where.append("user_id = %s")
            params.append(user_filter)
        clause = " AND ".join(where)
        with psycopg.connect(self.database_url) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM admin_question_activity WHERE {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT message_id,session_id,session_title,user_id,username,display_name,user_role,question,created_at "
                f"FROM admin_question_activity WHERE {clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        keys = ("message_id", "session_id", "session_title", "user_id", "username",
                "display_name", "user_role", "question", "created_at")
        return [dict(zip(keys, row)) for row in rows], total

    def list_admin_mistakes(self, search: str = "", status: str = "all", user_id: str = "",
                            knowledge_point_id: str = "",
                            limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        """List mistake records across students for the admin activity page."""
        limit, offset = max(1, min(limit, 100)), max(0, offset)
        search_pattern, user_filter = f"%{search.strip()}%", user_id.strip()
        where = ["(username ILIKE %s OR display_name ILIKE %s OR prompt ILIKE %s OR knowledge_point_id ILIKE %s)"]
        params: list[Any] = [search_pattern, search_pattern, search_pattern, search_pattern]
        if status.strip() and status != "all":
            where.append("status = %s")
            params.append(status.strip())
        if user_filter:
            where.append("user_id = %s")
            params.append(user_filter)
        if knowledge_point_id.strip():
            where.append("knowledge_point_id = %s")
            params.append(knowledge_point_id.strip())
        clause = " AND ".join(where)
        with psycopg.connect(self.database_url) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM admin_mistake_activity WHERE {clause}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT mistake_id,user_id,username,display_name,user_role,prompt,student_answer,correct_answer,"
                f"explanation,knowledge_point_id,source_message_id,status,created_at,reviewed_at "
                f"FROM admin_mistake_activity WHERE {clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        keys = ("mistake_id", "user_id", "username", "display_name", "user_role", "prompt",
                "student_answer", "correct_answer", "explanation", "knowledge_point_id",
                "source_message_id", "status", "created_at", "reviewed_at")
        return [dict(zip(keys, row)) for row in rows], total

    def admin_activity_counts(self) -> dict[str, int]:
        with psycopg.connect(self.database_url) as conn:
            question_count = conn.execute("SELECT COUNT(*) FROM admin_question_activity").fetchone()[0]
            mistake_count = conn.execute("SELECT COUNT(*) FROM admin_mistake_activity").fetchone()[0]
            pending_mistakes = conn.execute("SELECT COUNT(*) FROM admin_mistake_activity WHERE status='unreviewed'").fetchone()[0]
        return {"question_count": question_count, "mistake_count": mistake_count,
                "pending_mistakes": pending_mistakes}

    def admin_user_counts(self) -> dict[str, int]:
        with psycopg.connect(self.database_url) as conn:
            rows = conn.execute("SELECT role, status, COUNT(*) FROM admin_user_directory GROUP BY role, status").fetchall()
        counts = {"user_count": 0, "active_users": 0, "disabled_users": 0,
                  "student_count": 0, "teacher_count": 0, "admin_count": 0}
        for role, status, count in rows:
            counts["user_count"] += count
            counts["active_users" if status == "active" else "disabled_users"] += count
            key = {"student": "student_count", "teacher": "teacher_count", "admin": "admin_count"}.get(role)
            if key:
                counts[key] += count
        return counts

    def admin_activity_options(self) -> dict[str, list[dict[str, str]] | list[str]]:
        """Return existing students and knowledge points for admin dropdowns."""
        with psycopg.connect(self.database_url) as conn:
            students = conn.execute("""
                SELECT DISTINCT user_id, username, display_name
                FROM admin_question_activity
                WHERE user_role IN ('student', 'teacher')
                UNION
                SELECT DISTINCT user_id, username, display_name
                FROM admin_mistake_activity
                WHERE user_role IN ('student', 'teacher')
                ORDER BY display_name, username
            """).fetchall()
            points = conn.execute("""
                SELECT DISTINCT knowledge_point_id
                FROM admin_mistake_activity
                WHERE knowledge_point_id <> ''
                ORDER BY knowledge_point_id
            """).fetchall()
        return {
            "students": [{"user_id": row[0], "username": row[1], "display_name": row[2]} for row in students],
            "knowledge_points": [row[0] for row in points],
        }

    def update_user(self, user_id: str, *, role: str | None = None, status: str | None = None) -> User:
        if role is not None and role not in {"student", "teacher", "admin"}: raise AuthError("角色必须是 student、teacher 或 admin")
        if status is not None and status not in {"active", "disabled"}: raise AuthError("账号状态必须是 active 或 disabled")
        sets, vals = [], []
        if role is not None: sets += ["role=%s"]; vals += [role]
        if status is not None: sets += ["status=%s"]; vals += [status]
        if not sets: raise AuthError("没有要修改的字段")
        vals.append(user_id)
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute(f"UPDATE app_users SET {','.join(sets)},updated_at=NOW() WHERE user_id=%s RETURNING user_id,username,display_name,role", vals).fetchone()
        if not row: raise AuthError("用户不存在")
        return self._row_user(row)

    def claim_session(self, session_id: str, user_id: str) -> None:
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute("SELECT user_id FROM app_sessions WHERE session_id=%s", (session_id,)).fetchone()
            if row and row[0] != user_id: raise AuthError("无权访问该会话")
            conn.execute("INSERT INTO app_sessions(session_id,user_id) VALUES (%s,%s) ON CONFLICT (session_id) DO UPDATE SET updated_at=NOW()", (session_id, user_id))
            conn.execute("INSERT INTO learning_conversations(session_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (session_id, user_id))

    def owns_session(self, session_id: str, user_id: str) -> bool:
        with psycopg.connect(self.database_url) as conn: return conn.execute("SELECT 1 FROM app_sessions WHERE session_id=%s AND user_id=%s", (session_id, user_id)).fetchone() is not None
    def session_exists(self, session_id: str) -> bool:
        with psycopg.connect(self.database_url) as conn: return conn.execute("SELECT 1 FROM app_sessions WHERE session_id=%s", (session_id,)).fetchone() is not None
    def list_sessions(self, user_id: str) -> list[str]:
        with psycopg.connect(self.database_url) as conn: return [r[0] for r in conn.execute("SELECT session_id FROM app_sessions WHERE user_id=%s ORDER BY updated_at DESC", (user_id,)).fetchall()]
    def delete_session(self, session_id: str, user_id: str) -> bool:
        with psycopg.connect(self.database_url) as conn: return conn.execute("DELETE FROM app_sessions WHERE session_id=%s AND user_id=%s", (session_id, user_id)).rowcount == 1


class PostgresMemoryStore(MemoryStore):
    def __init__(self, database_url: str, ttl_seconds: int = 3600):
        self.database_url, self.ttl_seconds, self.embedding = database_url, ttl_seconds, HashEmbedding()
        ensure_runtime_schema(database_url)

    @property
    def long_term(self) -> dict[str, MemoryItem]:
        with psycopg.connect(self.database_url) as conn:
            rows = conn.execute("SELECT memory_id,student_id,content,memory_type,course_id,knowledge_point_id,confidence,importance,occurred_at,source_message_id,version,status FROM learning_memories ORDER BY occurred_at DESC").fetchall()
        return {r[0]: MemoryItem(*[str(v) if i in {8} and v is not None else v for i, v in enumerate(r)]) for r in rows}

    @property
    def mistakes(self) -> dict[str, dict]:
        with psycopg.connect(self.database_url) as conn:
            rows = conn.execute("SELECT mistake_id,student_id,prompt,student_answer,correct_answer,explanation,knowledge_point_id,source_message_id,status,created_at,reviewed_at FROM learning_mistakes").fetchall()
        keys=("mistake_id","student_id","prompt","student_answer","correct_answer","explanation","knowledge_point_id","source_message_id","status","created_at","reviewed_at")
        return {r[0]:dict(zip(keys,r)) for r in rows}

    @property
    def practice_stats(self) -> dict:
        with psycopg.connect(self.database_url) as conn:
            rows=conn.execute("SELECT student_id,knowledge_point_id,attempts,correct FROM learning_practice_stats").fetchall()
        result={}
        for student,point,attempts,correct in rows: result.setdefault(student,{})[point]={"attempts":attempts,"correct":correct}
        return result

    def append_message(self, session_id: str, role: str, content: str) -> None:
        with psycopg.connect(self.database_url) as conn:
            if not conn.execute("SELECT 1 FROM learning_conversations WHERE session_id=%s", (session_id,)).fetchone():
                synthetic = "runtime-session-" + hashlib.sha1(session_id.encode()).hexdigest()[:20]
                conn.execute("INSERT INTO app_users(user_id,username,display_name,password_hash,status) VALUES (%s,%s,%s,%s,'disabled') ON CONFLICT DO NOTHING", (synthetic, synthetic[:64], f"运行时会话 {session_id}", "migrated-account-no-login"))
                conn.execute("INSERT INTO app_sessions(session_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (session_id, synthetic))
                conn.execute("INSERT INTO learning_conversations(session_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (session_id, synthetic))
            conn.execute("INSERT INTO conversation_messages(session_id,role,content) VALUES (%s,%s,%s)", (session_id, role, content))
            conn.execute("UPDATE learning_conversations SET updated_at=NOW(),title=CASE WHEN title='新学习对话' AND %s='user' THEN LEFT(%s,32) ELSE title END WHERE session_id=%s", (role, content, session_id))
    def get_messages(self, session_id: str) -> list[dict]:
        with psycopg.connect(self.database_url) as conn:
            rows = conn.execute("SELECT role,content,created_at FROM conversation_messages WHERE session_id=%s ORDER BY created_at DESC LIMIT 20", (session_id,)).fetchall()
        return [{"role": r[0], "content": r[1], "at": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2])} for r in reversed(rows)]
    def delete_session(self, session_id: str) -> None:
        with psycopg.connect(self.database_url) as conn: conn.execute("DELETE FROM app_sessions WHERE session_id=%s", (session_id,))
    def session_summary(self, session_id: str) -> dict:
        msgs = self.get_messages(session_id); first = next((m["content"] for m in msgs if m["role"] == "user"), "新学习对话")
        return {"session_id": session_id, "title": first[:32], "message_count": len(msgs), "updated_at": msgs[-1]["at"] if msgs else ""}
    def write(self, student_id: str, content: str, **kwargs) -> MemoryItem:
        item_id = hashlib.sha1(f"{student_id}:{content}".encode()).hexdigest()[:16]
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute("SELECT memory_id,version FROM learning_memories WHERE memory_id=%s", (item_id,)).fetchone()
            vals = (student_id, content, kwargs.get("course_id", ""), kwargs.get("knowledge_point_id", ""), kwargs.get("confidence", .8), kwargs.get("importance", .5), kwargs.get("source_message_id", ""))
            if row:
                conn.execute("UPDATE learning_memories SET content=%s,course_id=%s,knowledge_point_id=%s,confidence=%s,importance=%s,source_message_id=%s,version=version+1,status='updated',updated_at=NOW() WHERE memory_id=%s", (content, vals[2], vals[3], vals[4], vals[5], vals[6], item_id))
            else:
                conn.execute("INSERT INTO learning_memories(memory_id,student_id,content,course_id,knowledge_point_id,confidence,importance,source_message_id,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())", (item_id, *vals))
        return self.long_term[item_id]
    def recall(self, student_id: str, query: str, top_k: int = 5) -> list[MemoryItem]:
        q = self.embedding.embed(query); rows = [(cosine(q, self.embedding.embed(i.content)) * (.5 + i.importance / 2), i) for i in self.long_term.values() if i.student_id == student_id and i.status != "deleted"]; rows.sort(key=lambda x:x[0], reverse=True); return [i for _,i in rows[:top_k]]
    def review(self, memory_id: str, action: str, content: str | None = None) -> MemoryItem:
        item = self.long_term.get(memory_id)
        if not item: raise KeyError(memory_id)
        if action == "delete": sql, vals = "status='deleted'", ()
        elif action in {"approve", "activate"}: sql, vals = "status='active'", ()
        elif action == "correct" and content and content.strip(): sql, vals = "content=%s,status='updated',version=version+1", (content.strip(),)
        else: raise ValueError("correct 操作需要 content" if action == "correct" else f"未知审核操作：{action}")
        with psycopg.connect(self.database_url) as conn: conn.execute(f"UPDATE learning_memories SET {sql},updated_at=NOW() WHERE memory_id=%s", (*vals, memory_id))
        return self.long_term[memory_id]
    def write_mistake(self, student_id: str, **kwargs) -> dict:
        mistake_id = hashlib.sha1(f"{student_id}:{kwargs.get('prompt','')}:{kwargs.get('source_message_id','')}".encode()).hexdigest()[:16]
        item = {"mistake_id": mistake_id, "student_id": student_id, **{k: kwargs.get(k, "") for k in ("prompt","student_answer","correct_answer","explanation","knowledge_point_id","source_message_id")}, "status":"unreviewed", "created_at":utc_now()}
        with psycopg.connect(self.database_url) as conn: conn.execute("INSERT INTO learning_mistakes(mistake_id,student_id,prompt,student_answer,correct_answer,explanation,knowledge_point_id,source_message_id) VALUES (%(mistake_id)s,%(student_id)s,%(prompt)s,%(student_answer)s,%(correct_answer)s,%(explanation)s,%(knowledge_point_id)s,%(source_message_id)s) ON CONFLICT (mistake_id) DO UPDATE SET prompt=EXCLUDED.prompt", item)
        return item
    def list_mistakes(self, student_id: str, *, include_reviewed: bool = True) -> list[dict]:
        sql = "SELECT mistake_id,student_id,prompt,student_answer,correct_answer,explanation,knowledge_point_id,source_message_id,status,created_at,reviewed_at FROM learning_mistakes WHERE student_id=%s" + ("" if include_reviewed else " AND status<>'reviewed'") + " ORDER BY created_at DESC"
        with psycopg.connect(self.database_url) as conn: rows = conn.execute(sql, (student_id,)).fetchall()
        keys=("mistake_id","student_id","prompt","student_answer","correct_answer","explanation","knowledge_point_id","source_message_id","status","created_at","reviewed_at")
        return [dict(zip(keys,r)) for r in rows]
    def review_mistake(self, mistake_id: str, student_id: str) -> dict:
        with psycopg.connect(self.database_url) as conn: row=conn.execute("UPDATE learning_mistakes SET status='reviewed',reviewed_at=NOW() WHERE mistake_id=%s AND student_id=%s RETURNING mistake_id,student_id,prompt,student_answer,correct_answer,explanation,knowledge_point_id,source_message_id,status,created_at,reviewed_at", (mistake_id,student_id)).fetchone()
        if not row: raise KeyError(mistake_id)
        keys=("mistake_id","student_id","prompt","student_answer","correct_answer","explanation","knowledge_point_id","source_message_id","status","created_at","reviewed_at"); return dict(zip(keys,row))
    def record_practice_result(self, student_id: str, knowledge_point_id: str, correct: bool) -> None:
        if not knowledge_point_id:return
        with psycopg.connect(self.database_url) as conn: conn.execute("INSERT INTO learning_practice_stats(student_id,knowledge_point_id,attempts,correct) VALUES (%s,%s,1,%s) ON CONFLICT (student_id,knowledge_point_id) DO UPDATE SET attempts=learning_practice_stats.attempts+1,correct=learning_practice_stats.correct+EXCLUDED.correct,updated_at=NOW()", (student_id,knowledge_point_id,int(correct)))
    def mastery(self, student_id: str) -> list[dict]:
        base = super().mastery(student_id)
        with psycopg.connect(self.database_url) as conn: stats=conn.execute("SELECT knowledge_point_id,attempts,correct FROM learning_practice_stats WHERE student_id=%s", (student_id,)).fetchall()
        # Merge persisted attempt counters into the same scoring contract.
        for point, attempts, correct in stats:
            row = next((x for x in base if x["knowledge_point"] == point), None)
            if row is None: base.append({"knowledge_point":point,"score":round(correct/attempts*70) if attempts else 50,"status":"学习中","signals":0,"mistakes":0})
        return sorted(base, key=lambda x:(x["score"],x["knowledge_point"]))
    def profile(self, student_id: str) -> dict:
        items=[i for i in self.long_term.values() if i.student_id==student_id and i.status!="deleted"]
        return {"student_id":student_id,"memory_count":len(items),"knowledge_points":sorted({i.knowledge_point_id for i in items if i.knowledge_point_id}),"recent_events":[i.content for i in sorted(items,key=lambda x:x.occurred_at,reverse=True)[:10]],"mastery":self.mastery(student_id)}
    def list_for_student(self, student_id: str, *, include_deleted: bool=False) -> list[MemoryItem]:
        return sorted([i for i in self.long_term.values() if i.student_id==student_id and (include_deleted or i.status!="deleted")], key=lambda x:x.occurred_at, reverse=True)


class PostgresCheckpointStore:
    def __init__(self, database_url: str): self.database_url=database_url; ensure_runtime_schema(database_url)
    def save(self, session_id: str, state: Any) -> None:
        payload=asdict(state); payload["memory_hits"]=[x.to_dict() if hasattr(x,"to_dict") else x for x in (state.memory_hits or [])]; payload["retrieval_hits"]=[x.to_dict() if hasattr(x,"to_dict") else x for x in (state.retrieval_hits or [])]
        with psycopg.connect(self.database_url) as conn: conn.execute("INSERT INTO agent_checkpoints(session_id,state) VALUES (%s,%s) ON CONFLICT(session_id) DO UPDATE SET state=EXCLUDED.state,updated_at=NOW()", (session_id,json.dumps(payload,ensure_ascii=False)))
    def load(self, session_id: str) -> dict | None:
        with psycopg.connect(self.database_url) as conn: row=conn.execute("SELECT state FROM agent_checkpoints WHERE session_id=%s", (session_id,)).fetchone()
        return row[0] if row else None
    def list_sessions(self) -> list[str]:
        with psycopg.connect(self.database_url) as conn:return [r[0] for r in conn.execute("SELECT session_id FROM agent_checkpoints ORDER BY updated_at DESC").fetchall()]
    def delete(self, session_id: str) -> None:
        with psycopg.connect(self.database_url) as conn: conn.execute("DELETE FROM agent_checkpoints WHERE session_id=%s", (session_id,))


class PostgresPracticeService(PracticeService):
    """Durable quiz and answer storage while retaining PracticeService's API."""
    def __init__(self, database_url: str, chunks: list):
        super().__init__(chunks); self.database_url=database_url; ensure_runtime_schema(database_url)
        with psycopg.connect(database_url) as conn:
            ensure_bank_table(conn)

    def create_quiz(self, student_id: str, grade_id: str = "grade7", count: int = 5,
                    difficulty: str | None = None, knowledge_point: str | None = None) -> PracticeQuiz:
        # Prefer the durable bank. The in-memory generator remains a safe fallback
        # for development databases that have not been seeded yet.
        bank_rows = []
        with psycopg.connect(self.database_url) as conn:
            clauses = ["grade_id=%s", "active=TRUE"]
            params: list[object] = [grade_id]
            if knowledge_point:
                clauses.append("knowledge_point=%s"); params.append(knowledge_point)
            if difficulty:
                clauses.append("difficulty=%s"); params.append(difficulty)
            bank_rows = conn.execute(
                f"SELECT question_id,knowledge_point,chapter,prompt,options,answer_index,explanation,citation,difficulty "
                f"FROM practice_question_bank WHERE {' AND '.join(clauses)} ORDER BY question_id", params
            ).fetchall()
        if len(bank_rows) < min(max(int(count), 1), 10) and difficulty:
            return self.create_quiz(student_id, grade_id, count, None, knowledge_point)
        if bank_rows:
            import random
            count = max(1, min(int(count), 10))
            rng = random.Random(f"{student_id}:{grade_id}:{knowledge_point or ''}:{len(self.quizzes)}")
            selected = rng.sample(bank_rows, min(count, len(bank_rows)))
            questions: list[PracticeQuestion] = []
            for number, row in enumerate(selected, 1):
                bank_id, point, chapter, prompt, options, answer, explanation, citation, row_difficulty = row
                if isinstance(options, str):
                    options = json.loads(options)
                citation = citation or {}
                chunk = Chunk(
                    document_id=str(citation.get("document_id", "")),
                    chunk_id=str(citation.get("content_id", bank_id)),
                    text=str(citation.get("excerpt", "")),
                    title=str(citation.get("title", point)),
                    chapter_id=str(chapter or citation.get("chapter", "")),
                    source_path=str(citation.get("source_path", "")),
                    knowledge_point_ids=[str(point)],
                    metadata={"grade_id": grade_id, "difficulty": row_difficulty, "bank_question_id": bank_id},
                )
                questions.append(PracticeQuestion(f"q{number}", str(prompt), list(options), int(answer), str(explanation), chunk))
            quiz = PracticeQuiz(secrets.token_urlsafe(12), student_id, grade_id, questions)
            self.quizzes[quiz.quiz_id] = quiz
        else:
            quiz = super().create_quiz(student_id, grade_id, count, difficulty, knowledge_point)
        with psycopg.connect(self.database_url) as conn:
            conn.execute("INSERT INTO practice_quizzes(quiz_id,student_id,grade_id) VALUES (%s,%s,%s)", (quiz.quiz_id,student_id,grade_id))
            for ordinal,q in enumerate(quiz.questions,1):
                citation={"chunk_id":q.chunk.chunk_id,"document_id":q.chunk.document_id,"title":q.chunk.title,"source_path":q.chunk.source_path,"excerpt":q.chunk.text[:240]}
                conn.execute("INSERT INTO practice_questions(question_id,quiz_id,ordinal,prompt,options,answer_index,explanation,citation,knowledge_point) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (q.question_id,quiz.quiz_id,ordinal,q.prompt,json.dumps(q.options,ensure_ascii=False),q.answer,q.explanation,json.dumps(citation,ensure_ascii=False),q.chunk.knowledge_point_ids[0] if q.chunk.knowledge_point_ids else q.chunk.title))
        return quiz

    def list_categories(self, grade_id: str) -> list[dict[str, str]]:
        with psycopg.connect(self.database_url) as conn:
            rows = conn.execute(
                "SELECT DISTINCT chapter, knowledge_point FROM practice_question_bank "
                "WHERE grade_id=%s AND active=TRUE ORDER BY chapter, knowledge_point",
                (grade_id,),
            ).fetchall()
        return [{"chapter": chapter or "未分类", "knowledge_point": point} for chapter, point in rows]

    def submit(self, quiz_id: str, student_id: str, answers: dict[str,int]) -> dict:
        with psycopg.connect(self.database_url) as conn:
            quizrow=conn.execute("SELECT grade_id,submitted FROM practice_quizzes WHERE quiz_id=%s AND student_id=%s FOR UPDATE",(quiz_id,student_id)).fetchone()
            if not quizrow or quizrow[1]: raise KeyError(quiz_id)
            rows=conn.execute("SELECT question_id,prompt,options,answer_index,explanation,citation,knowledge_point FROM practice_questions WHERE quiz_id=%s ORDER BY ordinal",(quiz_id,)).fetchall()
            results=[]; correct_count=0
            for qid,prompt,options,answer,explanation,citation,point in rows:
                selected=answers.get(qid); ok=selected==answer; correct_count+=int(ok)
                results.append({"question_id":qid,"selected":selected,"correct":answer,"is_correct":ok,"explanation":explanation,"citation":citation,"knowledge_point":point})
                conn.execute("INSERT INTO practice_attempts(quiz_id,question_id,student_id,selected_index,correct_index,is_correct) VALUES (%s,%s,%s,%s,%s,%s)", (quiz_id,qid,student_id,selected,answer,ok))
                conn.execute("UPDATE learning_practice_stats SET attempts=attempts+1,correct=correct+%s,updated_at=NOW() WHERE student_id=%s AND knowledge_point_id=%s", (int(ok),student_id,point))
                conn.execute("INSERT INTO learning_practice_stats(student_id,knowledge_point_id,attempts,correct) SELECT %s,%s,1,%s WHERE NOT EXISTS (SELECT 1 FROM learning_practice_stats WHERE student_id=%s AND knowledge_point_id=%s)", (student_id,point,int(ok),student_id,point))
            conn.execute("UPDATE practice_quizzes SET submitted=TRUE,submitted_at=NOW() WHERE quiz_id=%s",(quiz_id,))
        return {"quiz_id":quiz_id,"score":correct_count,"total":len(results),"results":results}
