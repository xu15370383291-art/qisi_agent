from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .conversation import ConversationRouter, RouteDecision
from .memory import LearningEventExtractor, MemoryConsolidator, MemoryStore
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
    # A structured, session-scoped action proposed by the assistant.  It is
    # intentionally separate from chat history so a short reply such as
    # “需要” can be resolved before any retrieval takes place.
    pending_action: dict | None = None
    route_decision: dict | None = None
    resolved_request: str = ""


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
                 checkpointer: CheckpointStore | None = None,
                 event_extractor: LearningEventExtractor | None = None,
                 conversation_router: ConversationRouter | None = None):
        self.rag = rag
        self.memory = memory or MemoryStore()
        self.checkpointer = checkpointer or CheckpointStore()
        self.event_extractor = event_extractor or LearningEventExtractor(getattr(rag, "chat", None))
        self.conversation_router = conversation_router or ConversationRouter(getattr(rag, "chat", None))

    def _persist_learning_event(self, user_id: str, session_id: str, query: str, answer: str = "") -> None:
        event = self.event_extractor.extract(query, answer=answer)
        if not event:
            return
        MemoryConsolidator(self.memory).persist(user_id, event, source_message_id=session_id)

    def _pending_action(self, session_id: str, history: list[dict]) -> dict | None:
        checkpoint = self.checkpointer.load(session_id) or {}
        return self.conversation_router.active_pending_action(checkpoint.get("pending_action"), history)

    @staticmethod
    def _clarification_answer(pending_action: dict | None) -> str:
        if not pending_action:
            return "我还不能确定你想继续哪一部分。请补充具体主题或想进行的操作。"
        options = pending_action.get("options") if isinstance(pending_action.get("options"), list) else []
        if len(options) > 1:
            return f"我想确认一下：你希望我提供“{options[0]}”，还是“{options[1]}”？"
        proposal = str(pending_action.get("proposal") or pending_action.get("resolved_request") or "刚才的内容")
        return f"我想确认一下：你是希望我继续“{proposal}”吗？也可以直接告诉我想调整的地方。"

    @staticmethod
    def _knowledge_points(citations: list) -> list[str]:
        points: list[str] = []
        for citation in citations:
            values = citation.get("knowledge_points", []) if isinstance(citation, dict) else getattr(citation, "knowledge_points", [])
            for point in values or []:
                if point and point not in points:
                    points.append(str(point))
        return points[:8]

    def _next_pending_action(self, history: list[dict], user_request: str, assistant_answer: str,
                             citations: list, course_id: str | None, grade_id: str | None) -> dict | None:
        action = self.conversation_router.extract_pending_action(
            history=history, user_request=user_request, assistant_answer=assistant_answer,
            course_id=course_id, grade_id=grade_id,
            knowledge_point_ids=self._knowledge_points(citations),
        )
        if action:
            action["created_at_user_turn"] = sum(1 for item in history if item.get("role") == "user") + 1
        return action

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
        """Compatibility entry point that shares the streaming conversation state machine."""
        done: dict[str, Any] = {}
        for item in self.run_stream(user_id, session_id, query, course_id=course_id, grade_id=grade_id):
            if item["type"] == "done":
                done = item
            elif item["type"] == "memory_update":
                self.persist_completed_turn(item["user_id"], item["session_id"], item["query"], item["answer"])
        state = self.checkpointer.load(session_id) or {}
        return ChatResult(
            str(state.get("answer", "")), list(done.get("citations", [])),
            float(done.get("confidence", state.get("confidence", 0.0))),
            bool(done.get("refused", False)), str(done.get("intent", state.get("intent", "qa"))),
            list(state.get("retrieval_hits", [])), str(done.get("source", "knowledge_base")),
            {"memory_hits": list(state.get("memory_hits", []))},
        )

    def conversation(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "messages": self.memory.get_messages(session_id),
            "checkpoint": self.checkpointer.load(session_id),
        }

    def persist_completed_turn(self, user_id: str, session_id: str, query: str, answer: str) -> None:
        """Write long-term learning evidence after a streamed answer is complete."""
        self._persist_learning_event(user_id, session_id, query, answer)

    def run_stream(self, user_id: str, session_id: str, query: str, *, course_id: str | None = None,
                   grade_id: str | None = None):
        """Yield retrieval metadata and model deltas, then persist the completed turn."""
        conversation_history = self.memory.get_messages(session_id)
        pending_action = self._pending_action(session_id, conversation_history)
        self.memory.append_message(session_id, "user", query)
        yield {"type": "progress", "stage": "understanding", "message": "正在理解你的问题"}
        decision = self.conversation_router.decide(conversation_history, pending_action, query)
        resolved_request = decision.resolved_request or query
        intent = self.route(resolved_request)
        state = AgentState(user_id, session_id, query, intent=intent, pending_action=pending_action,
                           route_decision=decision.to_dict(), resolved_request=resolved_request, memory_hits=[])
        self.checkpointer.save(session_id, state)

        if decision.relation in {"reject", "ambiguous"}:
            answer = ("好的，这一步先不继续。你有其他问题时随时告诉我。"
                      if decision.relation == "reject" else self._clarification_answer(pending_action))
            state.answer, state.confidence = answer, decision.confidence
            state.pending_action = pending_action if decision.relation == "ambiguous" else None
            self.checkpointer.save(session_id, state)
            self.memory.append_message(session_id, "assistant", answer)
            yield {"type": "meta", "intent": "conversation", "prepared": {"hits": [], "citations": [],
                   "source": "conversation", "confidence": decision.confidence}}
            yield {"type": "delta", "delta": answer}
            yield {"type": "done", "intent": "conversation", "confidence": decision.confidence,
                   "source": "conversation", "citations": []}
            return

        if intent == "learning_analytics" or not hasattr(self.rag, "prepare_stream"):
            if intent == "learning_analytics":
                profile = self.memory.profile(user_id)
                mastery = profile.get("mastery", [])
                weak = [item["knowledge_point"] for item in mastery if item["status"] == "薄弱"][:3]
                answer = (f"你目前记录了 {profile['memory_count']} 条学习线索，涉及 {len(profile['knowledge_points'])} 个知识点。"
                          + (f"建议优先复习：{'、'.join(weak)}。" if weak else "暂时没有明显薄弱知识点，继续保持练习。"))
                result = ChatResult(answer, [], 0.85, False, source="learning_profile")
            else:
                result = self.rag.answer(resolved_request, student_id=user_id, course_id=course_id, grade_id=grade_id)
            state.retrieval_hits = result.retrieval_hits
            state.memory_hits = result.student_context.get("memory_hits", []) if result.student_context else []
            state.answer, state.confidence = result.answer, result.confidence
            state.pending_action = self._next_pending_action(conversation_history, resolved_request, result.answer,
                                                             result.citations, course_id, grade_id)
            self.checkpointer.save(session_id, state)
            self.memory.append_message(session_id, "assistant", result.answer)
            yield {"type": "meta", "intent": intent,
                   "prepared": {"hits": result.retrieval_hits, "citations": result.citations,
                                "source": result.source, "confidence": result.confidence}}
            yield {"type": "delta", "delta": result.answer}
            yield {"type": "done", "intent": intent, "confidence": result.confidence,
                   "source": result.source, "citations": result.citations}
            yield {"type": "memory_update", "user_id": user_id, "session_id": session_id,
                   "query": query, "answer": result.answer}
            return

        yield {"type": "progress", "stage": "retrieving", "message": "正在搜索课程资料和学习记录"}
        prepared = self.rag.prepare_stream(resolved_request, student_id=user_id, course_id=course_id, grade_id=grade_id,
                                           conversation_history=conversation_history)
        state.retrieval_hits = prepared["hits"]
        state.memory_hits = prepared.get("student_context", {}).get("memory_hits", state.memory_hits)
        yield {"type": "progress", "stage": "organizing", "message": "正在整理回答依据"}
        yield {"type": "meta", "intent": intent, "prepared": prepared}
        answer = prepared["prefix"]
        yield {"type": "delta", "delta": prepared["prefix"]}
        if not prepared.get("refused", False):
            yield {"type": "progress", "stage": "generating", "message": "正在生成回答"}
            try:
                for delta in self.rag.stream_prepared(prepared):
                    answer += delta
                    yield {"type": "delta", "delta": delta}
            except Exception as exc:
                yield {"type": "error", "message": str(exc)}
                answer = "模型服务暂时不可用，请稍后重试。"
        state.answer = answer
        state.confidence = prepared["confidence"]
        yield {"type": "progress", "stage": "organizing", "message": "正在准备下一步学习"}
        state.pending_action = self._next_pending_action(conversation_history, resolved_request, answer,
                                                         prepared["citations"], course_id, grade_id)
        self.checkpointer.save(session_id, state)
        # Conversation state is critical to the next request, so it is durable
        # before notifying the browser that this turn has finished.  Only the
        # longer-running learning-memory extraction remains asynchronous.
        self.memory.append_message(session_id, "assistant", answer)
        yield {"type": "done", "intent": intent, "confidence": prepared["confidence"],
               "source": prepared["source"], "citations": prepared["citations"],
               "refused": prepared.get("refused", False)}
        yield {"type": "memory_update", "user_id": user_id, "session_id": session_id,
               "query": query, "answer": answer}
