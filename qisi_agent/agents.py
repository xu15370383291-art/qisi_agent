from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .memory import MemoryStore, extract_learning_event
from .models import ChatResult


@dataclass(slots=True)
class AgentState:
    user_id: str
    session_id: str
    query: str
    intent: str = "qa"
    memory_hits: list = None
    retrieval_hits: list = None
    answer: str = ""
    confidence: float = 0.0


class CheckpointStore:
    """LangGraph Checkpointer 的本地兼容实现，便于阶段 2 先验证恢复语义。"""

    def __init__(self):
        self._states: dict[str, dict] = {}

    def save(self, session_id: str, state: AgentState) -> None:
        payload = asdict(state)
        payload["memory_hits"] = [item.to_dict() if hasattr(item, "to_dict") else item
                                  for item in (state.memory_hits or [])]
        payload["retrieval_hits"] = [item.to_dict() if hasattr(item, "to_dict") else item
                                     for item in (state.retrieval_hits or [])]
        self._states[session_id] = payload

    def load(self, session_id: str) -> dict | None:
        return self._states.get(session_id)

    def list_sessions(self) -> list[str]:
        return list(self._states)


class Supervisor:
    def __init__(self, rag: Any, memory: MemoryStore | None = None,
                 checkpointer: CheckpointStore | None = None):
        self.rag = rag
        self.memory = memory or MemoryStore()
        self.checkpointer = checkpointer or CheckpointStore()

    @staticmethod
    def route(query: str) -> str:
        query = query.strip().lower()
        if any(key in query for key in ("错题", "错因", "为什么错")):
            return "wrong_answer"
        if any(key in query for key in ("出题", "练习题", "来几道题")):
            return "question_generator"
        if any(key in query for key in ("掌握", "薄弱", "学习情况")):
            return "learning_analytics"
        if any(key in query for key in ("计划", "安排学习", "怎么复习", "复习计划")):
            return "study_plan"
        return "qa"

    def run(self, user_id: str, session_id: str, query: str, *, course_id: str | None = None,
            grade_id: str | None = None) -> ChatResult:
        intent = self.route(query)
        state = AgentState(user_id, session_id, query, intent=intent)
        self.checkpointer.save(session_id, state)
        self.memory.append_message(session_id, "user", query)
        memory_hits = self.memory.recall(user_id, query)
        state.memory_hits = memory_hits
        if intent == "learning_analytics":
            profile = self.memory.profile(user_id)
            mastery = profile.get("mastery", [])
            weak = [item["knowledge_point"] for item in mastery if item["status"] == "薄弱"][:3]
            answer = (f"你目前记录了 {profile['memory_count']} 条学习线索，涉及 {len(profile['knowledge_points'])} 个知识点。"
                      + (f"建议优先复习：{'、'.join(weak)}。" if weak else "暂时没有明显薄弱知识点，继续保持练习。"))
            result = ChatResult(answer, [], 0.85, False, source="learning_profile")
        else:
            result = self.rag.answer(query, student_id=user_id, course_id=course_id, grade_id=grade_id)
        state.retrieval_hits = result.retrieval_hits
        state.answer = result.answer
        state.confidence = result.confidence
        self.checkpointer.save(session_id, state)
        result.intent = intent
        event = extract_learning_event(query)
        if event:
            self.memory.write(user_id, str(event["content"]), confidence=float(event["confidence"]),
                              importance=float(event["importance"]), source_message_id=session_id,
                              knowledge_point_id=str(event.get("knowledge_point_id", "")))
        self.memory.append_message(session_id, "assistant", result.answer)
        return result

    def conversation(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "messages": self.memory.get_messages(session_id),
            "checkpoint": self.checkpointer.load(session_id),
        }

    def run_stream(self, user_id: str, session_id: str, query: str, *, course_id: str | None = None,
                   grade_id: str | None = None):
        """Yield retrieval metadata and model deltas, then persist the completed turn."""
        intent = self.route(query)
        if intent == "learning_analytics" or not hasattr(self.rag, "prepare_stream"):
            result = self.run(user_id, session_id, query, course_id=course_id, grade_id=grade_id)
            yield {"type": "meta", "intent": result.intent,
                   "prepared": {"hits": result.retrieval_hits, "citations": result.citations,
                                "source": result.source, "confidence": result.confidence}}
            yield {"type": "delta", "delta": result.answer}
            yield {"type": "done", "intent": result.intent, "confidence": result.confidence,
                   "source": result.source, "citations": result.citations}
            return
        state = AgentState(user_id, session_id, query, intent=intent)
        self.checkpointer.save(session_id, state)
        self.memory.append_message(session_id, "user", query)
        state.memory_hits = self.memory.recall(user_id, query)
        prepared = self.rag.prepare_stream(query, student_id=user_id, course_id=course_id, grade_id=grade_id)
        state.retrieval_hits = prepared["hits"]
        yield {"type": "meta", "intent": intent, "prepared": prepared}
        answer = prepared["prefix"]
        yield {"type": "delta", "delta": prepared["prefix"]}
        try:
            for delta in self.rag.stream_prepared(prepared):
                answer += delta
                yield {"type": "delta", "delta": delta}
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            answer = "模型服务暂时不可用，请稍后重试。"
        state.answer = answer
        state.confidence = prepared["confidence"]
        self.checkpointer.save(session_id, state)
        event = extract_learning_event(query)
        if event:
            self.memory.write(user_id, str(event["content"]), confidence=float(event["confidence"]),
                              importance=float(event["importance"]), source_message_id=session_id,
                              knowledge_point_id=str(event.get("knowledge_point_id", "")))
        self.memory.append_message(session_id, "assistant", answer)
        yield {"type": "done", "intent": intent, "confidence": prepared["confidence"],
               "source": prepared["source"], "citations": prepared["citations"]}
