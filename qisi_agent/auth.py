"""SQLite-backed authentication for the local FastAPI service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class AuthError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class User:
    user_id: str
    username: str
    display_name: str
    role: str

    def to_dict(self) -> dict[str, str]:
        return {"user_id": self.user_id, "username": self.username,
                "display_name": self.display_name, "role": self.role}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AuthStore:
    def __init__(self, database_path: str | Path = "data/runtime/auth.sqlite3",
                 secret_key: str | None = None, token_ttl_seconds: int = 86400):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_ttl_seconds = token_ttl_seconds
        self.secret_path = self.database_path.with_name(self.database_path.name + ".secret")
        self.secret = self._load_secret(secret_key)
        self._init_db()

    def _load_secret(self, configured: str | None) -> bytes:
        if configured:
            return configured.encode("utf-8")
        if self.secret_path.exists():
            return self.secret_path.read_bytes()
        value = secrets.token_bytes(32)
        self.secret_path.write_bytes(value)
        try:
            os.chmod(self.secret_path, 0o600)
        except OSError:
            pass
        return value

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'student',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "status" not in columns:
                db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

    @staticmethod
    def _validate_credentials(username: str, password: str) -> tuple[str, str]:
        username = username.strip()
        if not 3 <= len(username) <= 64 or any(char.isspace() for char in username):
            raise AuthError("用户名需为 3-64 个不含空格的字符")
        if len(password) < 6 or len(password) > 128:
            raise AuthError("密码长度需为 6-128 个字符")
        return username, password

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        return f"pbkdf2_sha256$260000${_b64(salt)}${_b64(digest)}"

    @classmethod
    def _verify_password(cls, password: str, encoded: str) -> bool:
        try:
            algorithm, rounds, salt, expected = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(rounds))
            return hmac.compare_digest(actual, _unb64(expected))
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(row["user_id"], row["username"], row["display_name"], row["role"])

    def register(self, username: str, password: str, display_name: str = "",
                 role: str = "student") -> User:
        username, password = self._validate_credentials(username, password)
        if role not in {"student", "teacher"}:
            raise AuthError("公开注册只能选择学生或教师，管理员账号请由管理员创建")
        display_name = display_name.strip() or username
        if len(display_name) > 80:
            raise AuthError("显示名称不能超过 80 个字符")
        user_id = secrets.token_urlsafe(12)
        try:
            with self._connect() as db:
                db.execute("INSERT INTO users(user_id, username, display_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                           (user_id, username, display_name, self._hash_password(password), role))
        except sqlite3.IntegrityError as exc:
            raise AuthError("用户名已存在") from exc
        return User(user_id, username, display_name, role)

    def register_admin(self, username: str, password: str, display_name: str = "",
                       application_code: str = "", expected_code: str | None = None) -> User:
        """Create an administrator only after the server-side application code matches."""
        if not expected_code or not hmac.compare_digest(application_code.strip(), expected_code):
            raise AuthError("管理员申请码不正确")
        username, password = self._validate_credentials(username, password)
        display_name = display_name.strip() or username
        if len(display_name) > 80:
            raise AuthError("显示名称不能超过 80 个字符")
        user_id = secrets.token_urlsafe(12)
        try:
            with self._connect() as db:
                db.execute("INSERT INTO users(user_id, username, display_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
                           (user_id, username, display_name, self._hash_password(password), "admin"))
        except sqlite3.IntegrityError as exc:
            raise AuthError("用户名已存在") from exc
        return User(user_id, username, display_name, "admin")

    def authenticate(self, username: str, password: str, role: str | None = None) -> User:
        username = username.strip()
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
        if not row:
            raise AuthError("用户名或密码错误")
        if row["status"] == "disabled":
            raise AuthError("账号已停用，请联系管理员")
        if (role is not None and row["role"] != role) or not self._verify_password(password, row["password_hash"]):
            raise AuthError("用户名或密码错误")
        return self._row_to_user(row)

    def issue_token(self, user: User) -> str:
        now = int(time.time())
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        payload = _b64(json.dumps({"sub": user.user_id, "iat": now, "exp": now + self.token_ttl_seconds},
                                  separators=(",", ":")).encode())
        unsigned = f"{header}.{payload}"
        return f"{unsigned}.{_b64(hmac.new(self.secret, unsigned.encode(), hashlib.sha256).digest())}"

    def user_from_token(self, token: str) -> User:
        try:
            header, payload, signature = token.split(".", 2)
            unsigned = f"{header}.{payload}"
            expected = _b64(hmac.new(self.secret, unsigned.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(signature, expected):
                raise AuthError("访问令牌无效")
            data = json.loads(_unb64(payload))
            if int(data["exp"]) < int(time.time()):
                raise AuthError("访问令牌已过期，请重新登录")
            user_id = str(data["sub"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AuthError("访问令牌无效") from exc
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row or row["status"] != "active":
            raise AuthError("用户不存在")
        return self._row_to_user(row)

    def list_users(self, search: str = "", limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        limit = max(1, min(limit, 100)); offset = max(0, offset); pattern = f"%{search.strip()}%"
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM users WHERE username LIKE ? OR display_name LIKE ?",
                               (pattern, pattern)).fetchone()[0]
            rows = db.execute("SELECT user_id, username, display_name, role, status, created_at FROM users "
                              "WHERE username LIKE ? OR display_name LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              (pattern, pattern, limit, offset)).fetchall()
        return [dict(row) for row in rows], total

    def update_user(self, user_id: str, *, role: str | None = None, status: str | None = None) -> User:
        if role is not None and role not in {"student", "teacher", "admin"}:
            raise AuthError("角色必须是 student、teacher 或 admin")
        if status is not None and status not in {"active", "disabled"}:
            raise AuthError("账号状态必须是 active 或 disabled")
        fields = []; values = []
        if role is not None: fields.append("role = ?"); values.append(role)
        if status is not None: fields.append("status = ?"); values.append(status)
        if not fields: raise AuthError("没有要修改的字段")
        values.append(user_id)
        with self._connect() as db:
            result = db.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?", values)
            if result.rowcount != 1: raise AuthError("用户不存在")
            row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_user(row)

    def claim_session(self, session_id: str, user_id: str) -> None:
        if not session_id or len(session_id) > 128:
            raise AuthError("session_id 无效")
        with self._connect() as db:
            row = db.execute("SELECT user_id FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            if row and row["user_id"] != user_id:
                raise AuthError("无权访问该会话")
            db.execute("INSERT OR IGNORE INTO sessions(session_id, user_id) VALUES (?, ?)", (session_id, user_id))

    def owns_session(self, session_id: str, user_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?",
                             (session_id, user_id)).fetchone()
        return row is not None

    def session_exists(self, session_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return row is not None

    def list_sessions(self, user_id: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT session_id FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
        return [row["session_id"] for row in rows]

    def delete_session(self, session_id: str, user_id: str) -> bool:
        with self._connect() as db:
            result = db.execute("DELETE FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, user_id))
        return result.rowcount == 1
