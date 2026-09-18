from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv(".env", override=False)


def main() -> None:
    _load_environment()
    parser = argparse.ArgumentParser(description="启思学伴 PostgreSQL 知识库 CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="启动 PostgreSQL + pgvector SSE API")
    # Keep the default aligned with the documented local URL. Callers can
    # override this with --host when they explicitly need IPv6 or LAN access.
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    promote = sub.add_parser("promote-admin", help="将已有账号提升为管理员")
    promote.add_argument("--username", required=True)
    pg_import = sub.add_parser("pg-import", help="将本地知识库规范化导入 PostgreSQL")
    pg_import.add_argument("--source", default="data/corpus")
    pg_embed = sub.add_parser("pg-embed", help="使用百炼 Embedding 写入 PostgreSQL pgvector")
    pg_embed.add_argument("--force", action="store_true", help="重新生成全部向量")
    practice_seed = sub.add_parser("practice-seed", help="为每个年级知识点生成并写入练习题库")
    pg_search = sub.add_parser("pg-search", help="从 PostgreSQL 知识库检索并返回来源依据")
    pg_search.add_argument("--query", required=True)
    pg_search.add_argument("--grade", choices=("grade7", "grade8", "grade9"))
    pg_search.add_argument("--limit", type=int, default=10)
    worker = sub.add_parser("ingestion-worker", help="处理 PostgreSQL 中排队的文档导入任务")
    worker.add_argument("--poll-seconds", type=float, default=2.0)
    worker.add_argument("--once", action="store_true", help="只处理一个任务后退出")
    runtime_migrate = sub.add_parser("db-migrate-runtime", help="将本地认证和学习运行数据幂等迁移到 PostgreSQL")
    runtime_migrate.add_argument("--auth-db", default=None)
    runtime_migrate.add_argument("--memory-file", default="data/runtime/memory.json")
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        from .api import create_app
        from .auth import AuthStore
        from .cloud_rag import DashScopeRAGService
        from .dashscope import (DashScopeChatService, DashScopeConfig,
                                DashScopeEmbeddingService, DashScopeError)
        from .pg_runtime import PostgresAuthStore, PostgresMemoryStore, PostgresPracticeService, PostgresCheckpointStore
        from .pg_knowledge import database_url_from_env
        from .postgres_retrieval import PostgresHybridIndex
        from .reranker import cross_encoder_from_env
        try:
            config = DashScopeConfig.from_env()
            index = PostgresHybridIndex(
                database_url_from_env(), DashScopeEmbeddingService(config),
                reranker=cross_encoder_from_env(),
            )
            rag = DashScopeRAGService(index, DashScopeChatService(config))
        except (DashScopeError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        database_url = database_url_from_env()
        memory = PostgresMemoryStore(database_url)
        auth = PostgresAuthStore(database_url, secret_key=os.getenv("AUTH_SECRET_KEY") or None,
                                  token_ttl_seconds=int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "86400")))
        checkpointer = PostgresCheckpointStore(database_url)
        practice = PostgresPracticeService(database_url, index.chunks)
        uvicorn.run(create_app(rag, memory=memory, auth=auth, checkpointer=checkpointer, practice=practice),
                    host=args.host, port=args.port)
    elif args.command == "promote-admin":
        from .auth import AuthError
        from .pg_runtime import PostgresAuthStore
        from .pg_knowledge import database_url_from_env
        auth = PostgresAuthStore(
            database_url_from_env(), secret_key=os.getenv("AUTH_SECRET_KEY") or None,
            token_ttl_seconds=int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "86400")),
        )
        items, _ = auth.list_users(search=args.username, limit=100)
        matches = [item for item in items if item["username"].lower() == args.username.lower()]
        if not matches:
            raise SystemExit("找不到该用户名，请先注册账号")
        try:
            user = auth.update_user(matches[0]["user_id"], role="admin", status="active")
        except AuthError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({"user_id": user.user_id, "username": user.username,
                          "role": user.role}, ensure_ascii=False))
    elif args.command == "pg-import":
        from .pg_knowledge import database_url_from_env, sync_corpus
        from .pg_runtime import ensure_runtime_schema
        from .question_bank import seed_question_bank
        database_url = database_url_from_env()
        ensure_runtime_schema(database_url)
        result = sync_corpus(database_url, args.source)
        # The first import creates the knowledge tables; apply the additive
        # content-lineage migration immediately after that bootstrap as well.
        ensure_runtime_schema(database_url)
        result["practice_bank"] = seed_question_bank(database_url)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "pg-embed":
        from .dashscope import DashScopeConfig
        from .pg_knowledge import database_url_from_env, embed_corpus
        config = DashScopeConfig.from_env()
        print(json.dumps(embed_corpus(
            database_url_from_env(), model=config.embedding_model,
            dimensions=config.embedding_dimensions,
            batch_size=config.embedding_batch_size, force=args.force,
        ), ensure_ascii=False))
    elif args.command == "practice-seed":
        from .pg_knowledge import database_url_from_env
        from .question_bank import seed_question_bank
        print(json.dumps(seed_question_bank(database_url_from_env()), ensure_ascii=False))
    elif args.command == "pg-search":
        from .dashscope import DashScopeConfig, DashScopeEmbeddingService, DashScopeError
        from .pg_knowledge import database_url_from_env
        from .postgres_retrieval import PostgresHybridIndex
        from .reranker import cross_encoder_from_env
        try:
            config = DashScopeConfig.from_env()
            index = PostgresHybridIndex(
                database_url_from_env(), DashScopeEmbeddingService(config),
                reranker=cross_encoder_from_env(),
            )
        except (DashScopeError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        hits = index.search(args.query, args.limit, grade_id=args.grade)
        print(json.dumps({"query": args.query,
                          "route": hits[0].chunk.metadata.get("retrieval_route") if hits else None,
                          "hits": [hit.to_dict() for hit in hits]},
                         ensure_ascii=False, indent=2))
    elif args.command == "ingestion-worker":
        from .async_ingestion import run_worker
        run_worker(poll_seconds=args.poll_seconds, once=args.once)
    elif args.command == "db-migrate-runtime":
        from .pg_runtime import ensure_runtime_schema
        import sqlite3
        import psycopg
        from .pg_knowledge import database_url_from_env
        from .models import utc_now
        auth_db = args.auth_db or os.getenv("AUTH_DB_PATH", "data/runtime/auth.sqlite3")
        ensure_runtime_schema(database_url_from_env())
        with psycopg.connect(database_url_from_env()) as conn:
            src = sqlite3.connect(auth_db); src.row_factory = sqlite3.Row
            users = src.execute("SELECT user_id,username,display_name,password_hash,role,status,created_at FROM users").fetchall()
            sessions = src.execute("SELECT session_id,user_id,created_at FROM sessions").fetchall()
            for row in users:
                conn.execute("INSERT INTO app_users(user_id,username,display_name,password_hash,role,status,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,display_name=EXCLUDED.display_name,password_hash=EXCLUDED.password_hash,role=EXCLUDED.role,status=EXCLUDED.status", tuple(row))
            for row in sessions:
                conn.execute("INSERT INTO app_sessions(session_id,user_id,created_at) VALUES (%s,%s,%s) ON CONFLICT(session_id) DO NOTHING", tuple(row)); conn.execute("INSERT INTO learning_conversations(session_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (row[0],row[1]))
            src.close()
            payload=json.loads(Path(args.memory_file).read_text(encoding="utf-8")) if Path(args.memory_file).exists() else {}
            # Preserve historical records that predate authentication by creating
            # non-login placeholder users, keeping all runtime rows referentially valid.
            referenced_students = {item.get("student_id") for item in payload.get("long_term", []) if item.get("student_id")}
            referenced_students.update(item.get("student_id") for item in payload.get("mistakes", []) if item.get("student_id"))
            referenced_students.update(payload.get("practice_stats", {}).keys())
            for student_id in referenced_students:
                exists = conn.execute("SELECT 1 FROM app_users WHERE user_id=%s", (student_id,)).fetchone()
                if not exists:
                    username = f"migrated_{student_id}"[:64]
                    conn.execute("INSERT INTO app_users(user_id,username,display_name,password_hash,role,status) VALUES (%s,%s,%s,%s,'student','disabled') ON CONFLICT DO NOTHING", (student_id, username, f"历史学生 {student_id}", "migrated-account-no-login"))
            for item in payload.get("long_term",[]):
                conn.execute("INSERT INTO learning_memories(memory_id,student_id,content,memory_type,course_id,knowledge_point_id,confidence,importance,occurred_at,source_message_id,version,status) VALUES (%(memory_id)s,%(student_id)s,%(content)s,%(memory_type)s,%(course_id)s,%(knowledge_point_id)s,%(confidence)s,%(importance)s,%(occurred_at)s,%(source_message_id)s,%(version)s,%(status)s) ON CONFLICT(memory_id) DO UPDATE SET content=EXCLUDED.content,version=EXCLUDED.version,status=EXCLUDED.status", item)
            for item in payload.get("mistakes",[]):
                conn.execute("INSERT INTO learning_mistakes(mistake_id,student_id,prompt,student_answer,correct_answer,explanation,knowledge_point_id,source_message_id,status,created_at,reviewed_at) VALUES (%(mistake_id)s,%(student_id)s,%(prompt)s,%(student_answer)s,%(correct_answer)s,%(explanation)s,%(knowledge_point_id)s,%(source_message_id)s,%(status)s,%(created_at)s,%(reviewed_at)s) ON CONFLICT(mistake_id) DO NOTHING", {**item,"reviewed_at":item.get("reviewed_at")})
            for session_id, messages in payload.get("short_term",{}).items():
                if not conn.execute("SELECT 1 FROM learning_conversations WHERE session_id=%s", (session_id,)).fetchone():
                    synthetic_user = "migrated-session-" + hashlib.sha1(session_id.encode()).hexdigest()[:20]
                    conn.execute("INSERT INTO app_users(user_id,username,display_name,password_hash,role,status) VALUES (%s,%s,%s,%s,'student','disabled') ON CONFLICT DO NOTHING", (synthetic_user, synthetic_user[:64], f"历史会话 {session_id}", "migrated-account-no-login"))
                    conn.execute("INSERT INTO app_sessions(session_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (session_id, synthetic_user))
                    conn.execute("INSERT INTO learning_conversations(session_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (session_id, synthetic_user))
                for message in messages[-20:]:
                    conn.execute("INSERT INTO conversation_messages(session_id,role,content,created_at) SELECT %s,%s,%s,%s WHERE NOT EXISTS (SELECT 1 FROM conversation_messages WHERE session_id=%s AND role=%s AND content=%s AND created_at=%s)", (session_id,message.get("role","user"),message.get("content",""),message.get("at") or utc_now(),session_id,message.get("role","user"),message.get("content",""),message.get("at") or utc_now()))
            for student, points in payload.get("practice_stats",{}).items():
                for point, stats in points.items():
                    conn.execute("INSERT INTO learning_practice_stats(student_id,knowledge_point_id,attempts,correct) VALUES (%s,%s,%s,%s) ON CONFLICT(student_id,knowledge_point_id) DO UPDATE SET attempts=EXCLUDED.attempts,correct=EXCLUDED.correct", (student,point,stats.get("attempts",0),stats.get("correct",0)))
        print(json.dumps({"users":len(users),"sessions":len(sessions),"memories":len(payload.get("long_term",[])),"mistakes":len(payload.get("mistakes",[])),"messages":sum(len(v[-20:]) for v in payload.get("short_term",{}).values()),"status":"ok"},ensure_ascii=False))


if __name__ == "__main__":
    main()
