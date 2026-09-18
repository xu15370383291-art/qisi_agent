import json
from pathlib import Path

from qisi_agent.agents import CheckpointStore, Supervisor
from qisi_agent.api import create_app
from qisi_agent.auth import AuthError, AuthStore
from qisi_agent.graph import KnowledgeGraph
from qisi_agent.cloud_rag import DashScopeRAGService
from qisi_agent.dashscope import DashScopeChatService, DashScopeConfig, DashScopeEmbeddingService
from qisi_agent.embeddings import meaningful_overlap, tokenize
from qisi_agent.ingestion import load_corpus
from qisi_agent.memory import MemoryStore
from qisi_agent.practice import PracticeService
from qisi_agent.models import ChatResult, Chunk, Citation, RetrievalHit
from qisi_agent.reranker import CrossEncoderReranker, PassthroughReranker, cross_encoder_from_env


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
