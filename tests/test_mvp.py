import json
from pathlib import Path

import pytest

from qisi_agent.agents import AgentState, CheckpointStore, Supervisor
from qisi_agent.api import create_app
from qisi_agent.auth import AuthError, AuthStore, _unb64
from qisi_agent.graph import KnowledgeGraph
from qisi_agent.cloud_rag import DashScopeRAGService
from qisi_agent.conversation import ConversationRouter, RouteDecision
from qisi_agent.dashscope import DashScopeChatService, DashScopeConfig, DashScopeEmbeddingService
from qisi_agent.embeddings import meaningful_overlap, tokenize
from qisi_agent.ingestion import load_corpus
from qisi_agent.memory import LearningEventExtractor, MemoryConsolidator, MemoryDecayService, MemoryStore
from qisi_agent.practice import PracticeService
from qisi_agent.models import ChatResult, Chunk, Citation, RetrievalHit
from qisi_agent.pg_knowledge import SCHEMA_SQL
from qisi_agent.async_ingestion import (recover_stale_jobs, retry_document_job,
                                        upload_document_id, validate_upload_payload)
from qisi_agent.reranker import CrossEncoderReranker, PassthroughReranker, cross_encoder_from_env
from qisi_agent.student_context import (StudentContextRetriever, _student_context_schema_sql,
                                        memory_embedding_text, mistake_embedding_text)
from qisi_agent.student_context import StudentContext


ROOT = Path(__file__).parents[1]


class _FixtureIndex:
    """Small non-persistent index stub for API/service tests."""

    embedding_model = "fixture"

    def __init__(self, chunks):
        self.chunks = chunks

    def search(self, query, top_k=5, *, student_id=None, course_id=None, grade_id=None):
        query_tokens = set(tokenize(query)) - set("的了是和与或请问吗呢什么怎么如何这那一个")
        hits = []
        for chunk in self.chunks:
            if grade_id and chunk.metadata.get("grade_id") != grade_id:
                continue
            searchable = " ".join((chunk.title, chunk.chapter_id, chunk.text,
                                   *chunk.knowledge_point_ids))
            overlap = meaningful_overlap(query, searchable)
            lexical = sum(token in tokenize(searchable) for token in query_tokens)
            if not overlap and not lexical:
                continue
            score = float(overlap + lexical)
            hits.append(RetrievalHit(chunk, score, min(1.0, score / 4), score))
        hits.sort(key=lambda item: item.score, reverse=True)
        for rank, hit in enumerate(hits[:top_k], 1):
            hit.rank = rank
            hit.chunk.metadata["meaningful_overlap"] = meaningful_overlap(query, " ".join((hit.chunk.text, hit.chunk.title, *hit.chunk.knowledge_point_ids)))
        return hits[:top_k]


class _FixtureRAG:
    def __init__(self, index):
        self.index = index
        self.similarity_threshold = 0.35

    def answer(self, query, *, top_k=5, student_id=None, course_id=None, grade_id=None):
        hits = self.index.search(query, top_k, student_id=student_id,
                                 course_id=course_id, grade_id=grade_id)
        usable = [hit for hit in hits if hit.chunk.metadata.get("meaningful_overlap", 0) > 0
                  or hit.vector_score >= self.similarity_threshold]
        if not usable:
            return ChatResult("当前知识库没有足够依据回答这个问题，我先不做猜测。", [], 0.0,
                              True, retrieval_hits=hits)
        citations = [Citation(hit.chunk.chunk_id, hit.chunk.document_id, hit.chunk.source_path,
                              hit.chunk.title, hit.chunk.text[:240]) for hit in usable[:3]]
        return ChatResult(f"关于“{query}”，教材中的相关要点是：{usable[0].chunk.text[:160]}。",
                          citations, 0.8, retrieval_hits=hits)


class _FakeCrossEncoder:
    def predict(self, pairs, **kwargs):
        assert kwargs == {"batch_size": 2, "show_progress_bar": False,
                          "convert_to_numpy": True}
        return [-2.0 if "无关" in document else 3.0 for _, document in pairs]


def test_cross_encoder_reranker_uses_pair_scores_as_final_order():
    irrelevant = RetrievalHit(Chunk("doc-1", "chunk-1", "无关内容", title="干扰项"), 0.9)
    relevant = RetrievalHit(Chunk("doc-2", "chunk-2", "分数约分需要除以公因数",
                                  title="分数约分", knowledge_point_ids=["约分"]), 0.1)
    reranker = CrossEncoderReranker("fake-model", batch_size=2, model=_FakeCrossEncoder())
    hits = reranker.rerank("分数怎样约分？", [irrelevant, relevant])
    assert [hit.chunk.chunk_id for hit in hits] == ["chunk-2", "chunk-1"]
    assert hits[0].score == hits[0].reranker_score
    assert hits[0].chunk.metadata["retrieval_score"] == 0.1
    assert hits[0].chunk.metadata["reranker"] == "cross-encoder:fake-model"


def test_reranker_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    assert isinstance(cross_encoder_from_env(), PassthroughReranker)


def test_reranker_falls_back_when_model_load_fails(monkeypatch):
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.delenv("RERANKER_REQUIRED", raising=False)

    def fail_to_load(*args, **kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr("qisi_agent.reranker.CrossEncoderReranker", fail_to_load)
    assert isinstance(cross_encoder_from_env(), PassthroughReranker)


def test_student_context_hybrid_retrieval_is_student_scoped_and_filters_old_states():
    store = MemoryStore()
    relevant = store.write("student-a", "解一元一次方程时移项符号总是写错",
                           knowledge_point_id="一元一次方程", confidence=0.9, importance=0.9)
    stale = store.write("student-a", "分数约分需要复习", knowledge_point_id="分数约分")
    store.long_term[stale.memory_id].status = "superseded"
    store.write("student-b", "解一元一次方程时移项符号总是写错",
                knowledge_point_id="一元一次方程")
    store.write_mistake("student-a", prompt="解方程 2(x-1)=6 时移项漏变号",
                        explanation="移项后符号需要改变", knowledge_point_id="一元一次方程")
    store.record_practice_result("student-a", "一元一次方程", False)

    context = StudentContextRetriever(store).retrieve("student-a", "我解方程移项为什么总是符号错？")

    assert context.memory_hits and context.memory_hits[0].chunk.chunk_id == relevant.memory_id
    assert all(hit.chunk.metadata["student_id"] == "student-a" for hit in context.memory_hits)
    assert stale.memory_id not in [hit.chunk.chunk_id for hit in context.memory_hits]
    assert context.mistake_hits and context.mistake_hits[0].chunk.metadata["source_type"] == "student_mistake"
    assert context.practice_evidence[0]["knowledge_point_id"] == "一元一次方程"


def test_student_context_reranker_reorders_memory_candidates_after_hybrid_recall():
    store = MemoryStore()
    store.write("student", "学生对分数约分不熟悉", knowledge_point_id="分数约分")
    target = store.write("student", "学生在一元一次方程移项时容易写错符号",
                         knowledge_point_id="一元一次方程")

    class ContextCrossEncoder:
        def predict(self, pairs, **kwargs):
            return [5.0 if "移项" in document else -5.0 for _, document in pairs]

    reranker = CrossEncoderReranker("fixture-context", batch_size=2, model=ContextCrossEncoder())
    context = StudentContextRetriever(store, reranker=reranker).retrieve("student", "方程怎么移项？")

    assert context.memory_hits[0].chunk.chunk_id == target.memory_id
    assert context.memory_hits[0].reranker_score > context.memory_hits[-1].reranker_score


def test_student_context_embedding_schema_and_source_text_keep_evidence_types_separate():
    schema = _student_context_schema_sql(8)
    assert "learning_memory_embeddings" in schema and "learning_mistake_embeddings" in schema
    assert "embedding vector(8)" in schema
    memory_text = memory_embedding_text({"knowledge_point_id": "一元一次方程", "content": "移项易错", "status": "active"})
    mistake_text = mistake_embedding_text({"knowledge_point_id": "一元一次方程", "prompt": "2x=4", "explanation": "两边同除 2"})
    assert "类型：学习记忆" in memory_text and "类型：错题记录" in mistake_text


def test_llm_learning_event_extraction_uses_validated_json_and_falls_back_safely():
    class EventChat:
        def complete(self, messages):
            assert "只返回一个 JSON 对象" in messages[-1]["content"]
            return type("Response", (), {"content": json.dumps({
                "relevant": True, "content": "学生在一元一次方程移项时容易漏变号",
                "memory_type": "difficulty_signal", "knowledge_point_id": "一元一次方程",
                "claim": "needs_review", "confidence": 0.88, "importance": 0.82,
            }, ensure_ascii=False)})()

    event = LearningEventExtractor(EventChat()).extract("我解方程移项总是错")
    assert event and event["extraction_method"] == "llm_json_schema"
    assert event["semantic_key"] == "一元一次方程:difficulty_signal:needs_review"

    class BadEventChat:
        def complete(self, messages):
            return type("Response", (), {"content": "不是 JSON"})()

    fallback = LearningEventExtractor(BadEventChat()).extract("我不会分数约分")
    assert fallback and fallback["extraction_method"] == "rule_fallback"


def test_memory_consolidation_preserves_history_and_excludes_conflicts_from_recall():
    store = MemoryStore()
    merger = MemoryConsolidator(store)
    old = merger.persist("student", {"content": "学生不会一元一次方程移项", "memory_type": "difficulty_signal",
                                      "knowledge_point_id": "一元一次方程", "semantic_key": "eq:difficulty",
                                      "confidence": .9, "importance": .8, "claim": "needs_review"}, source_message_id="m1")
    new = merger.persist("student", {"content": "学生已能完成一元一次方程移项", "memory_type": "difficulty_signal",
                                      "knowledge_point_id": "一元一次方程", "semantic_key": "eq:difficulty",
                                      "confidence": .9, "importance": .8, "claim": "mastered"}, source_message_id="m2")
    assert store.long_term[old.memory_id].status == "superseded"
    assert new.status == "active" and new.supersedes_memory_id == old.memory_id
    assert store.recall("student", "方程移项")[0].memory_id == new.memory_id
    same = merger.persist("student", {"content": new.content, "memory_type": "difficulty_signal",
                                       "knowledge_point_id": "一元一次方程", "semantic_key": "eq:difficulty",
                                       "confidence": .9, "importance": .8}, source_message_id="m3")
    assert same.memory_id == new.memory_id and same.version == 2
    conflict = merger.persist("student", {"content": "学生明确表示方程移项已经完全掌握", "memory_type": "difficulty_signal",
                                           "knowledge_point_id": "一元一次方程", "semantic_key": "eq:difficulty",
                                           "confidence": .9, "importance": .8, "conflicts_with_prior": True}, source_message_id="m4")
    assert conflict.status == "conflict"
    assert conflict.memory_id not in [item.memory_id for item in store.recall("student", "方程移项")]


def test_memory_decay_archives_old_signals_and_reactivates_when_reinforced():
    store = MemoryStore()
    item = store.write("student", "学生不会分数约分", memory_type="difficulty_signal",
                       knowledge_point_id="分数约分", confidence=.9)
    item.occurred_at = "2025-01-01T00:00:00+00:00"
    result = MemoryDecayService(store).apply()
    assert result["archived"] == 1 and item.status == "archived"
    # An exact, newly observed signal is not a deletion reversal; it is a new
    # reinforcement of the retained record and becomes retrievable again.
    reactivated = MemoryConsolidator(store).persist("student", {
        "content": "学生不会分数约分", "memory_type": "difficulty_signal", "knowledge_point_id": "分数约分",
        "semantic_key": "fraction:difficulty", "confidence": .9, "importance": .8,
    }, source_message_id="new-turn")
    assert reactivated.memory_id == item.memory_id and reactivated.status == "updated"


def test_learning_memory_lifecycle_migration_preserves_history_and_all_retrieval_states():
    migration = (ROOT / "migrations" / "005_learning_memory_lifecycle.sql").read_text(encoding="utf-8")
    assert "learning_memory_evidence" in migration
    assert "student_knowledge_states" in migration
    for state in ("superseded", "stale", "archived", "conflict"):
        assert f"'{state}'" in migration


def test_dual_rag_retrieves_and_injects_student_context_separately_from_course_evidence():
    class ContextRetriever:
        def __init__(self):
            self.calls = []

        def retrieve(self, student_id, query, *, top_k):
            self.calls.append((student_id, query, top_k))
            memory = RetrievalHit(Chunk("student-memory", "mem-1", "学生在移项时容易写错符号",
                                        title="学习记忆", knowledge_point_ids=["一元一次方程"],
                                        metadata={"source_type": "student_memory"}), 0.9)
            mistake = RetrievalHit(Chunk("student-mistake", "mistake-1", "2(x-1)=6 的去括号步骤出错",
                                         title="相关错题", knowledge_point_ids=["一元一次方程"],
                                         metadata={"source_type": "student_mistake"}), 0.8)
            return StudentContext([memory], [mistake], [{"knowledge_point_id": "一元一次方程", "mastery_status": "学习中"}])

    context_retriever = ContextRetriever()
    chunks = load_corpus(ROOT / "data" / "corpus")
    service = DashScopeRAGService(_FixtureIndex(chunks), DashScopeChatService(
        DashScopeConfig(api_key="test-key", embedding_dimensions=4), client=_FakeChatClient()),
        student_context_retriever=context_retriever)

    prepared = service.prepare_stream("为什么我总在一元一次方程移项时写错符号？", student_id="student-1")

    prompt = prepared["messages"][-1]["content"]
    assert context_retriever.calls == [("student-1", "为什么我总在一元一次方程移项时写错符号？", 5)]
    assert "本地教材上下文" in prompt and "【学生学习背景】" in prompt and "【相关错题线索】" in prompt
    assert "简短确认" in prepared["messages"][1]["content"]
    assert prepared["student_context"]["memory_hits"][0]["chunk"]["metadata"]["source_type"] == "student_memory"
    assert prepared["refused"] is False


def test_personalized_question_refuses_to_guess_without_student_evidence():
    class EmptyContextRetriever:
        def retrieve(self, student_id, query, *, top_k):
            return StudentContext()

    chunks = load_corpus(ROOT / "data" / "corpus")
    service = DashScopeRAGService(_FixtureIndex(chunks), DashScopeChatService(
        DashScopeConfig(api_key="test-key", embedding_dimensions=4), client=_FakeChatClient()),
        student_context_retriever=EmptyContextRetriever())

    prepared = service.prepare_stream("为什么我总在一元一次方程移项时写错符号？", student_id="student-1")

    assert prepared["refused"] is True
    assert prepared["source"] == "insufficient_personal_evidence"
    assert not prepared["messages"]


def test_stream_exposes_concise_progress_before_answer_generation(tmp_path):
    class StreamingRag:
        index = _FixtureIndex([])

        def prepare_stream(self, query, **kwargs):
            return {"hits": [], "citations": [], "source": "hybrid", "prefix": "来源：教材\n",
                    "confidence": 0.8, "refused": False, "student_context": {"memory_hits": []}}

        def stream_prepared(self, prepared):
            yield "这是根据依据整理的回答。"

    from fastapi.testclient import TestClient
    auth = AuthStore(tmp_path / "progress.sqlite3", secret_key="test-secret")
    user = auth.register("progress_student", "secure-pass")
    memory = MemoryStore()
    client = TestClient(create_app(StreamingRag(), memory=memory, auth=auth))
    response = client.post("/api/chat/stream", headers={"Authorization": f"Bearer {auth.issue_token(user)}"},
                           json={"query": "一元一次方程怎么解？", "session_id": "progress-session"})

    body = response.text
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.content.decode("utf-8") == body
    assert "这是根据依据整理的回答。" in body
    assert "\ufffd" not in body
    assert "event: progress" in body
    assert "正在搜索课程资料和学习记录" in body
    assert body.index("event: progress") < body.index("event: answer_delta")
    assert "event: memory_hit" not in body and "event: retrieval" not in body
    assert memory.get_messages("progress-session")[-1]["role"] == "assistant"

    deferred_store = MemoryStore()
    events = list(Supervisor(StreamingRag(), deferred_store).run_stream("student", "deferred", "一元一次方程怎么解？"))
    types = [item["type"] for item in events]
    assert types.index("done") < types.index("memory_update")
    assert deferred_store.get_messages("deferred")[-1]["role"] == "assistant"


def test_follow_up_receives_prior_answer_and_completed_answer_is_saved_before_done():
    class FollowUpRag:
        index = _FixtureIndex([])

        def __init__(self):
            self.histories = []

        def prepare_stream(self, query, *, conversation_history=None, **kwargs):
            self.histories.append(list(conversation_history or []))
            return {"hits": [], "citations": [], "source": "hybrid", "prefix": "",
                    "confidence": .8, "refused": False, "student_context": {"memory_hits": []}}

        def stream_prepared(self, prepared):
            yield "我可以画图或出一道小练习帮你巩固。"

    store = MemoryStore()
    rag = FollowUpRag()
    supervisor = Supervisor(rag, store)
    list(supervisor.run_stream("student", "follow-up", "请解释移项"))
    assert [(item["role"], item["content"]) for item in store.get_messages("follow-up")] == [
        ("user", "请解释移项"), ("assistant", "我可以画图或出一道小练习帮你巩固。"),
    ]
    list(supervisor.run_stream("student", "follow-up", "可以"))
    assert rag.histories[1][-1]["role"] == "assistant"
    assert rag.histories[1][-1]["content"] == "我可以画图或出一道小练习帮你巩固。"


def test_postgres_conversation_uses_message_id_for_stable_fast_turn_order(monkeypatch):
    """A timestamp tie must not place the assistant reply ahead of its question."""
    from qisi_agent.pg_runtime import PostgresMemoryStore
    import qisi_agent.pg_runtime as pg_runtime

    captured = {}

    class Cursor:
        def fetchall(self):
            # PostgreSQL returns the newest message first for the query below.
            return [
                ("assistant", "第二个回答", "2026-09-21T10:00:00"),
                ("user", "第二个问题", "2026-09-21T10:00:00"),
                ("assistant", "第一个回答", "2026-09-21T10:00:00"),
                ("user", "第一个问题", "2026-09-21T10:00:00"),
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, params
            return Cursor()

    class Psycopg:
        @staticmethod
        def connect(_url):
            return Connection()

    monkeypatch.setattr(pg_runtime, "psycopg", Psycopg())
    store = PostgresMemoryStore.__new__(PostgresMemoryStore)
    store.database_url = "postgresql://fixture"

    assert [(item["role"], item["content"]) for item in store.get_messages("fast-turns")] == [
        ("user", "第一个问题"), ("assistant", "第一个回答"),
        ("user", "第二个问题"), ("assistant", "第二个回答"),
    ]
    assert "ORDER BY message_id DESC" in captured["sql"]
    assert captured["params"] == ("fast-turns",)


def test_checkpoint_preserves_structured_pending_action_for_a_session():
    checkpoint = CheckpointStore()
    action = {
        "type": "practice_offer",
        "status": "pending",
        "topic": "平行线模型",
        "resolved_request": "围绕平行线模型出三道练习题并解析。",
        "expires_after_turns": 2,
    }
    checkpoint.save("pending-session", AgentState(
        "student", "pending-session", "什么是平行线模型？",
        pending_action=action,
    ))

    restored = checkpoint.load("pending-session")
    assert restored["pending_action"] == action
    assert restored["resolved_request"] == ""


def test_postgres_checkpoint_serializes_pending_action_as_session_state(monkeypatch):
    import qisi_agent.pg_runtime as pg_runtime
    from qisi_agent.pg_runtime import PostgresCheckpointStore

    captured = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, params

    class Psycopg:
        @staticmethod
        def connect(_url):
            return Connection()

    monkeypatch.setattr(pg_runtime, "psycopg", Psycopg())
    store = PostgresCheckpointStore.__new__(PostgresCheckpointStore)
    store.database_url = "postgresql://fixture"
    store.save("postgres-pending", AgentState(
        "student", "postgres-pending", "需要", pending_action={
            "type": "practice_offer", "status": "pending", "topic": "平行线模型",
        },
    ))

    payload = json.loads(captured["params"][1])
    assert "agent_checkpoints" in captured["sql"]
    assert payload["pending_action"]["topic"] == "平行线模型"


def test_conversation_router_resolves_acceptance_before_retrieval():
    class RouterChat:
        def complete(self, _messages):
            return type("Response", (), {"content": json.dumps({
                "relation": "accept", "confidence": 0.98,
                "resolved_request": "围绕平行线模型出三道由浅入深的练习题并解析。",
                "reason": "用户接受上一轮出题提议",
            }, ensure_ascii=False)})()

    router = ConversationRouter(RouterChat())
    history = [
        {"role": "user", "content": "什么是平行线模型？"},
        {"role": "assistant", "content": "我可以围绕平行线模型出三道题，你需要吗？"},
    ]
    pending = {
        "type": "practice_offer", "status": "pending", "topic": "平行线模型",
        "resolved_request": "围绕平行线模型出三道由浅入深的练习题并解析。",
        "created_at_user_turn": 1, "expires_after_turns": 2,
    }

    decision = router.decide(history, pending, "需要")

    assert decision.relation == "accept"
    assert decision.resolved_request.startswith("围绕平行线模型")
    assert "一元一次方程" not in decision.resolved_request


def test_conversation_router_extracts_structured_pending_practice_action():
    class ExtractorChat:
        def complete(self, _messages):
            return type("Response", (), {"content": json.dumps({"pending_action": {
                "type": "practice_offer", "proposal": "出三道平行线模型练习题",
                "topic": "平行线模型",
                "resolved_request": "围绕平行线模型出三道由浅入深的练习题并解析。",
                "options": [],
            }}, ensure_ascii=False)})()

    action = ConversationRouter(ExtractorChat()).extract_pending_action(
        history=[], user_request="什么是平行线模型？",
        assistant_answer="平行线模型可以这样理解。需要我出三道练习题吗？",
        grade_id="grade7", course_id="math", knowledge_point_ids=["平行线判定"],
    )

    assert action["status"] == "pending"
    assert action["type"] == "practice_offer"
    assert action["knowledge_point_ids"] == ["平行线判定"]
    assert action["resolved_request"].startswith("围绕平行线模型")


def test_conversation_router_requires_clarification_for_unselected_multiple_options():
    class OverconfidentRouterChat:
        def complete(self, _messages):
            return type("Response", (), {"content": json.dumps({
                "relation": "accept", "confidence": 0.99,
                "resolved_request": "继续刚才的讲解。",
            }, ensure_ascii=False)})()

    decision = ConversationRouter(OverconfidentRouterChat()).decide(
        [{"role": "user", "content": "什么是平行线模型？"}],
        {
            "type": "followup_offer", "status": "pending", "created_at_user_turn": 1,
            "expires_after_turns": 2, "resolved_request": "继续讲解平行线模型。",
            "options": ["代码示例", "架构图"],
        },
        "需要",
    )

    assert decision.relation == "ambiguous"
    assert decision.reason == "unselected_multiple_options"


def test_router_failure_with_pending_action_fails_safe_without_retrieval():
    pending = {
        "type": "practice_offer", "status": "pending", "created_at_user_turn": 1,
        "expires_after_turns": 2, "resolved_request": "围绕平行线模型出题。",
    }
    decision = ConversationRouter().decide(
        [{"role": "user", "content": "什么是平行线模型？"}], pending, "需要",
    )

    assert decision.relation == "ambiguous"
    assert decision.reason == "router_unavailable"


def test_pending_acceptance_uses_resolved_topic_for_rag_not_raw_confirmation():
    class RoutedRag:
        index = _FixtureIndex([])

        def __init__(self):
            self.queries = []

        def prepare_stream(self, query, **kwargs):
            self.queries.append((query, kwargs.get("conversation_history")))
            return {"hits": [], "citations": [], "source": "hybrid", "prefix": "",
                    "confidence": 0.9, "refused": False, "student_context": {"memory_hits": []}}

        def stream_prepared(self, _prepared):
            yield "第 1 题：请根据同位角相等判断两直线平行。"

    class PendingRouter:
        def active_pending_action(self, pending_action, _history):
            return pending_action

        def decide(self, _history, _pending_action, user_message):
            assert user_message == "需要"
            return RouteDecision("accept", 0.99, "围绕平行线模型出三道由浅入深的练习题并解析。")

        def extract_pending_action(self, **_kwargs):
            return None

    memory = MemoryStore()
    memory.append_message("parallel-session", "user", "什么是平行线模型？")
    memory.append_message("parallel-session", "assistant", "平行线模型可以这样理解。需要我出题吗？")
    checkpoint = CheckpointStore()
    checkpoint.save("parallel-session", AgentState(
        "student", "parallel-session", "什么是平行线模型？", pending_action={
            "type": "practice_offer", "status": "pending", "topic": "平行线模型",
            "resolved_request": "围绕平行线模型出三道由浅入深的练习题并解析。",
            "created_at_user_turn": 1, "expires_after_turns": 2,
        },
    ))
    rag = RoutedRag()
    events = list(Supervisor(rag, memory, checkpoint, conversation_router=PendingRouter()).run_stream(
        "student", "parallel-session", "需要", course_id="math", grade_id="grade7",
    ))

    assert rag.queries[0][0] == "围绕平行线模型出三道由浅入深的练习题并解析。"
    assert rag.queries[0][0] != "需要"
    assert "一元一次方程" not in rag.queries[0][0]
    assert memory.get_messages("parallel-session")[-2]["content"] == "需要"
    assert "同位角" in memory.get_messages("parallel-session")[-1]["content"]
    assert checkpoint.load("parallel-session")["pending_action"] is None
    assert events[-2]["type"] == "done"


def test_streamed_answer_persists_model_extracted_pending_action():
    class StateExtractorChat:
        def __init__(self):
            self.calls = 0

        def complete(self, _messages):
            self.calls += 1
            return type("Response", (), {"content": json.dumps({"pending_action": {
                "type": "practice_offer", "proposal": "出三道平行线模型练习题",
                "topic": "平行线模型",
                "resolved_request": "围绕平行线模型出三道由浅入深的练习题并解析。",
                "options": [],
            }}, ensure_ascii=False)})()

    class OfferRag:
        index = _FixtureIndex([])

        def __init__(self):
            self.chat = StateExtractorChat()

        def prepare_stream(self, _query, **_kwargs):
            return {"hits": [], "citations": [], "source": "hybrid", "prefix": "",
                    "confidence": 0.9, "refused": False, "student_context": {"memory_hits": []}}

        def stream_prepared(self, _prepared):
            yield "平行线模型的关键是利用角的关系。需要我出三道练习题吗？"

    checkpoint = CheckpointStore()
    rag = OfferRag()
    events = list(Supervisor(rag, MemoryStore(), checkpoint).run_stream(
        "student", "offer-session", "什么是平行线模型？", course_id="math", grade_id="grade7",
    ))

    pending = checkpoint.load("offer-session")["pending_action"]
    # A short first question is classified once, then the finished answer is
    # inspected once for a structured follow-up action.
    assert rag.chat.calls == 2
    assert pending["status"] == "pending"
    assert pending["topic"] == "平行线模型"
    assert pending["created_at_user_turn"] == 1
    assert any(item["type"] == "progress" and item["message"] == "正在准备下一步学习" for item in events)


def test_question_bank_preserves_options_and_answer(tmp_path):
    path = tmp_path / "grade7_questions.json"
    path.write_text(json.dumps([{
        "question_id": "q1", "question": "1+1=?", "options": ["1", "2", "3"],
        "answer": "B", "explanation": "两个一相加等于二", "knowledge_point": "整数运算"
    }], ensure_ascii=False), encoding="utf-8")
    chunk = __import__("qisi_agent.ingestion", fromlist=["parse_question_bank"]).parse_question_bank(path)[0]
    assert chunk.metadata["options"] == ["1", "2", "3"]
    assert chunk.metadata["answer_index"] == 1


def test_practice_records_accuracy_in_learning_profile():
    from qisi_agent.models import Chunk
    from qisi_agent.practice import PracticeService
    store = MemoryStore()
    chunk = Chunk("d", "q", "题目", title="题目", knowledge_point_ids=["整数运算"],
                  metadata={"grade_id": "grade7", "options": ["1", "2"], "answer_index": 1,
                            "question_stem": "1+1=?", "explanation": "二"})
    quiz = PracticeService([chunk]).create_quiz("s", count=1)
    result = PracticeService([chunk])
    del result
    store.record_practice_result("s", "整数运算", True)
    store.record_practice_result("s", "整数运算", False)
    assert store.profile("s")["mastery"][0]["score"] == 35


def make_rag() -> _FixtureRAG:
    return _FixtureRAG(_FixtureIndex(load_corpus(ROOT / "data" / "corpus")))


def test_retrieval_has_traceable_source():
    index = make_rag().index
    result = index.search("分数怎样约分？")
    assert result and result[0].chunk.source_path.endswith("math_grade7.md")


def test_retrieval_can_filter_by_grade():
    index = make_rag().index
    grade8 = index.search("平行四边形", grade_id="grade8")
    assert grade8 and all(hit.chunk.metadata["grade_id"] == "grade8" for hit in grade8)
    assert index.search("平行四边形", grade_id="grade7")


def test_practice_quiz_submission_records_wrong_answer():
    service = PracticeService(make_rag().index.chunks)
    quiz = service.create_quiz("student-practice", "grade8", count=3)
    assert len(quiz.questions) == 3
    result = service.submit(quiz.quiz_id, "student-practice", {})
    assert result["score"] == 0 and result["total"] == 3
    assert all(not item["is_correct"] and item["citation"]["document_id"] for item in result["results"])


def test_conversation_management_is_user_scoped(tmp_path):
    from fastapi.testclient import TestClient
    auth = AuthStore(tmp_path / "sessions.sqlite3", secret_key="test-secret")
    app = create_app(make_rag(), memory=MemoryStore(), auth=auth)
    client = TestClient(app)
    one = client.post("/api/auth/register", json={"username": "session_one", "password": "secure-pass"}).json()
    two = client.post("/api/auth/register", json={"username": "session_two", "password": "secure-pass"}).json()
    h1 = {"Authorization": f"Bearer {one['access_token']}"}
    h2 = {"Authorization": f"Bearer {two['access_token']}"}
    created = client.post("/api/conversations", headers=h1).json()
    assert created["message_count"] == 0
    assert any(item["session_id"] == created["session_id"] for item in client.get("/api/conversations", headers=h1).json()["items"])
    assert client.get(f"/api/conversations/{created['session_id']}", headers=h2).status_code == 403
    assert client.delete(f"/api/conversations/{created['session_id']}", headers=h1).status_code == 200
    assert client.get(f"/api/conversations/{created['session_id']}", headers=h1).status_code == 404


def test_mistake_book_is_structured_and_user_scoped(tmp_path):
    from fastapi.testclient import TestClient
    auth = AuthStore(tmp_path / "mistakes.sqlite3", secret_key="test-secret")
    memory = MemoryStore()
    app = create_app(make_rag(), memory=memory, auth=auth)
    client = TestClient(app)
    one = client.post("/api/auth/register", json={"username": "mistake_one", "password": "secure-pass"}).json()
    two = client.post("/api/auth/register", json={"username": "mistake_two", "password": "secure-pass"}).json()
    h1 = {"Authorization": f"Bearer {one['access_token']}"}
    h2 = {"Authorization": f"Bearer {two['access_token']}"}
    item = memory.write_mistake(one["user"]["user_id"], prompt="平行四边形", explanation="检查对边条件",
                                 knowledge_point_id="平行四边形")
    assert client.get(f"/api/students/{one['user']['user_id']}/mistakes", headers=h1).json()["items"][0]["mistake_id"] == item["mistake_id"]
    assert client.get(f"/api/students/{one['user']['user_id']}/mistakes", headers=h2).status_code == 403
    assert client.post(f"/api/mistakes/{item['mistake_id']}/review", headers=h1).json()["status"] == "reviewed"


def test_learning_profile_exposes_mastery_status():
    store = MemoryStore()
    store.write("mastery-student", "掌握平行四边形", knowledge_point_id="平行四边形")
    store.write_mistake("mastery-student", prompt="平行四边形", explanation="检查对边", knowledge_point_id="平行四边形")
    profile = store.profile("mastery-student")
    assert profile["mastery"] and profile["mastery"][0]["knowledge_point"] == "平行四边形"
    assert profile["mastery"][0]["mistakes"] == 1


def test_teacher_student_detail_requires_teacher_role(tmp_path):
    from fastapi.testclient import TestClient
    auth = AuthStore(tmp_path / "teacher-detail.sqlite3", secret_key="test-secret")
    memory = MemoryStore()
    app = create_app(make_rag(), memory=memory, auth=auth)
    client = TestClient(app)
    student = client.post("/api/auth/register", json={"username": "detail_student", "password": "secure-pass"}).json()
    teacher = client.post("/api/auth/register", json={"username": "detail_teacher", "password": "secure-pass", "role": "teacher"}).json()
    student_id = student["user"]["user_id"]
    memory.write_mistake(student_id, prompt="一元一次方程", knowledge_point_id="一元一次方程")
    hs = {"Authorization": f"Bearer {student['access_token']}"}
    ht = {"Authorization": f"Bearer {teacher['access_token']}"}
    assert client.get(f"/api/teacher/students/{student_id}", headers=hs).status_code == 403
    detail = client.get(f"/api/teacher/students/{student_id}", headers=ht)
    assert detail.status_code == 200
    assert detail.json()["mistakes"][0]["knowledge_point_id"] == "一元一次方程"


def test_rag_citation_and_out_of_knowledge_refusal():
    service = make_rag()
    answer = service.answer("分数怎样约分？")
    assert not answer.refused
    assert answer.citations and answer.citations[0].document_id
    equation = service.answer("一元一次方程怎么解？")
    assert not equation.refused
    assert equation.citations[0].title == "一元一次方程"
    refused = service.answer("火星的天气预报是什么？")
    assert refused.refused
    assert not refused.citations


def test_memory_dedup_and_supervisor_route():
    store = MemoryStore()
    first = store.write("student-1", "分数约分总是出错", source_message_id="m1")
    second = store.write("student-1", "分数约分总是出错", source_message_id="m2")
    assert first.memory_id == second.memory_id
    assert second.version == 2
    checkpoint = CheckpointStore()
    supervisor = Supervisor(make_rag(), store, checkpoint)
    result = supervisor.run("student-1", "session-1", "这道错题为什么错？")
    assert result.intent == "wrong_answer"
    assert store.get_messages("session-1")[-1]["role"] == "assistant"
    assert checkpoint.load("session-1")["intent"] == "wrong_answer"
    conversation = supervisor.conversation("session-1")
    assert conversation["messages"][-1]["role"] == "assistant"
    assert conversation["checkpoint"]["retrieval_hits"]


def test_memory_review_and_profile():
    store = MemoryStore()
    item = store.write("student-2", "需要复习分数", knowledge_point_id="分数约分")
    assert store.review(item.memory_id, "approve").status == "active"
    assert store.profile("student-2")["knowledge_points"] == ["分数约分"]
    store.review(item.memory_id, "delete")
    assert store.profile("student-2")["memory_count"] == 0


def test_memory_persistence(tmp_path):
    path = tmp_path / "memory.json"
    first = MemoryStore(storage_path=path)
    item = first.write("student-3", "复习一元一次方程", knowledge_point_id="一元一次方程")
    first.append_message("session-3", "user", "复习一元一次方程")
    second = MemoryStore(storage_path=path)
    assert second.list_for_student("student-3")[0].memory_id == item.memory_id
    assert second.get_messages("session-3")[0]["content"] == "复习一元一次方程"


def test_auth_registration_tokens_and_user_isolation(tmp_path):
    from fastapi.testclient import TestClient

    auth = AuthStore(tmp_path / "auth.sqlite3", secret_key="test-secret", token_ttl_seconds=3600)
    first = auth.register("student_one", "secure-pass", "学生一")
    assert auth.user_from_token(auth.issue_token(first)).user_id == first.user_id
    try:
        auth.register("student_one", "secure-pass")
    except AuthError:
        pass
    else:
        raise AssertionError("duplicate username should be rejected")

    app = create_app(make_rag(), memory=MemoryStore(), auth=auth)
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "student_one", "password": "secure-pass"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=headers).json()["username"] == "student_one"
    assert client.post("/api/chat/stream", json={"query": "分数怎样约分？", "session_id": "owned-session"}).status_code == 401
    assert client.post("/api/chat/stream", headers=headers,
                       json={"query": "分数怎样约分？", "session_id": "owned-session"}).status_code == 200
    assert client.get(f"/api/students/{first.user_id}/learning-profile", headers=headers).status_code == 200
    assert client.get("/api/students/another-user/learning-profile", headers=headers).status_code == 403
    assert client.get("/api/teacher/overview", headers=headers).status_code == 403


def test_role_based_jwt_lifetimes_and_account_security_actions(tmp_path):
    import time
    auth = AuthStore(tmp_path / "token-policy.sqlite3", secret_key="test-secret")
    student = auth.register("token_student", "secure-pass")
    teacher = auth.register("token_teacher", "secure-pass", role="teacher")
    admin = auth.register_admin("token_admin", "secure-pass", application_code="code", expected_code="code")

    def claims(token):
        return json.loads(_unb64(token.split(".")[1]))

    assert claims(auth.issue_token(student, remember_me=True))["exp"] - int(time.time()) <= 7 * 24 * 3600
    assert claims(auth.issue_token(student))["exp"] - int(time.time()) <= 8 * 3600
    assert claims(auth.issue_token(teacher, remember_me=True))["exp"] - int(time.time()) <= 8 * 3600
    assert claims(auth.issue_token(admin, remember_me=True))["exp"] - int(time.time()) <= 2 * 3600

    old = auth.issue_token(student)
    auth.change_password(student.user_id, "secure-pass", "changed-pass")
    with pytest.raises(AuthError):
        auth.user_from_token(old)
    fresh = auth.issue_token(auth.authenticate("token_student", "changed-pass"))
    auth.revoke_all_tokens(student.user_id)
    with pytest.raises(AuthError):
        auth.user_from_token(fresh)


def test_login_remember_me_is_student_only_and_failures_are_limited(tmp_path):
    auth = AuthStore(tmp_path / "login-policy.sqlite3", secret_key="test-secret")
    student = auth.register("login_student", "secure-pass")
    auth.register("login_teacher", "secure-pass", role="teacher")
    client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(create_app(make_rag(), auth=auth))
    student_login = client.post("/api/auth/login", json={"username": "login_student", "password": "secure-pass", "role": "student", "remember_me": True})
    assert student_login.status_code == 200 and student_login.json()["persistent_login"] is True
    teacher_login = client.post("/api/auth/login", json={"username": "login_teacher", "password": "secure-pass", "role": "teacher", "remember_me": True})
    assert teacher_login.status_code == 200 and teacher_login.json()["persistent_login"] is False
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "login_student", "password": "wrong-pass", "role": "student"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "login_student", "password": "wrong-pass", "role": "student"}).status_code == 429


def test_auth_roles_are_explicit(tmp_path):
    from fastapi.testclient import TestClient

    auth = AuthStore(tmp_path / "roles.sqlite3", secret_key="test-secret")
    app = create_app(make_rag(), auth=auth)
    client = TestClient(app)
    assert client.post("/api/auth/register", json={"username": "role_student", "password": "secure-pass", "role": "student"}).json()["user"]["role"] == "student"
    assert client.post("/api/auth/register", json={"username": "role_teacher", "password": "secure-pass", "role": "teacher"}).json()["user"]["role"] == "teacher"
    assert client.post("/api/auth/register", json={"username": "role_admin", "password": "secure-pass", "role": "admin"}).status_code == 400
    assert client.post("/api/auth/login", json={"username": "role_student", "password": "secure-pass", "role": "teacher"}).status_code == 401


def test_disabled_account_reports_disabled_status(tmp_path):
    auth = AuthStore(tmp_path / "disabled.sqlite3", secret_key="test-secret")
    user = auth.register("disabled_user", "secure-pass", "停用用户")
    auth.update_user(user.user_id, status="disabled")
    try:
        auth.authenticate("disabled_user", "secure-pass")
    except AuthError as exc:
        assert str(exc) == "账号已停用，请联系管理员"
    else:
        raise AssertionError("disabled account should be rejected with a status message")


def test_structured_question_bank_ingestion(tmp_path):
    question_file = tmp_path / "questions.jsonl"
    question_file.write_text('{"question_id":"q-1","question":"解方程 2x=4","answer":"x=2","explanation":"两边同除以 2","knowledge_point":"一元一次方程","difficulty":"基础"}\n', encoding="utf-8")
    chunks = load_corpus(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].metadata["question_id"] == "q-1"
    assert "答案：x=2" in chunks[0].text
    assert chunks[0].knowledge_point_ids == ["一元一次方程"]


def test_content_import_view_is_part_of_postgres_schema():
    required_fragments = (
        "CREATE OR REPLACE VIEW public.admin_content_imports",
        "FROM public.ingestion_jobs j",
        "JOIN public.knowledge_document_versions v USING (version_id)",
        "JOIN public.knowledge_documents d USING (document_id)",
        "j.retry_count",
        "v.status AS version_status",
    )
    assert all(fragment in SCHEMA_SQL for fragment in required_fragments)


def test_failed_content_import_can_be_requeued(monkeypatch, tmp_path):
    source = tmp_path / "教材.md"
    source.write_text("# 一元一次方程\n", encoding="utf-8")

    class FakeCursor:
        def __init__(self):
            self.result = ("failed", "version-1", "document-1", str(source))
            self.updated = ("job-1", 2)
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=()):
            self.statements.append((statement, params))
            if "RETURNING job_id, retry_count" in statement:
                self.result = self.updated

        def fetchone(self):
            result, self.result = self.result, None
            return result

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self.cursor_instance

    connection = FakeConnection()
    monkeypatch.setattr("qisi_agent.async_ingestion.psycopg.connect", lambda database_url: connection)

    result = retry_document_job("fixture-db", "job-1")

    assert result == {"job_id": "job-1", "status": "queued", "retry_count": 2}
    statements = "\n".join(statement for statement, _ in connection.cursor_instance.statements)
    assert "retry_count=retry_count+1" in statements
    assert "UPDATE knowledge_document_versions" in statements
    assert "UPDATE knowledge_documents" in statements


def test_uploaded_document_id_is_stable_for_new_versions():
    first = upload_document_id("七年级数学.md", "grade7")
    second = upload_document_id("七年级数学.md", "grade7")
    other_grade = upload_document_id("七年级数学.md", "grade8")
    assert first == second
    assert first != other_grade
    assert first.startswith("doc_")


def test_upload_validation_rejects_bad_structured_content():
    assert validate_upload_payload("../教材.md", "# 章节\n内容".encode("utf-8")) == "教材.md"
    with pytest.raises(ValueError, match="有效的 JSON"):
        validate_upload_payload("题库.json", b"{not-json}")
    with pytest.raises(ValueError, match="空白文件"):
        validate_upload_payload("教材.txt", b" \n")
    with pytest.raises(ValueError, match="支持"):
        validate_upload_payload("教材.exe", b"data")


def test_stale_imports_are_marked_failed(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.statements = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=()):
            self.statements.append((statement, params))

        def fetchall(self):
            return [("job-timeout",)]

    class FakeConnection:
        def __init__(self):
            self.cursor_instance = FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self.cursor_instance

    connection = FakeConnection()
    monkeypatch.setattr("qisi_agent.async_ingestion.psycopg.connect", lambda database_url: connection)

    assert recover_stale_jobs("fixture-db", 60) == 1
    statements = "\n".join(statement for statement, _ in connection.cursor_instance.statements)
    assert "started_at < NOW()" in statements
    assert "stage='timeout'" in statements
    assert "UPDATE knowledge_document_versions" in statements


def test_graph_expansion():
    graph = KnowledgeGraph()
    graph.add("分数约分", "依赖", "最大公因数")
    assert ("分数约分", "依赖", "最大公因数") in graph.expand(["分数约分"])


class _FakeEmbeddingClient:
    class embeddings:
        @staticmethod
        def create(**kwargs):
            size = kwargs["dimensions"]
            rows = [type("Item", (), {"index": index, "embedding": [float(index + 1)] + [0.0] * (size - 1)})
                    for index, _ in enumerate(kwargs["input"])]
            return type("Response", (), {"data": list(reversed(rows))})


class _FakeChatClient:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                return type("Response", (), {"model": kwargs["model"], "choices": [type("Choice", (), {
                    "message": type("Message", (), {"content": "根据教材，方程两边可进行相同运算。"})()
                })()]})


def test_dashscope_adapters_and_cloud_rag():
    config = DashScopeConfig(api_key="test-key", embedding_dimensions=4, embedding_batch_size=2)
    embedding = DashScopeEmbeddingService(config, client=_FakeEmbeddingClient())
    vectors = embedding.embed_texts(["第一段", "第二段"])
    assert len(vectors) == 2 and len(vectors[0]) == 4 and vectors[1][0] == 2.0
    chat = DashScopeChatService(config, client=_FakeChatClient())
    assert chat.complete([{"role": "user", "content": "问题"}]).content.startswith("根据教材")
    chunks = load_corpus(ROOT / "data" / "corpus")[:2]
    index = _FixtureIndex(chunks)
    answer = DashScopeRAGService(index, chat).answer("一元一次方程怎么解？")
    assert not answer.refused and answer.citations and answer.source == "hybrid"
    general = DashScopeRAGService(index, chat, similarity_threshold=2.1).answer("请介绍太阳系。")
    assert not general.refused and not general.citations and general.source == "model"
    assert "未命中本地知识库" in general.answer
