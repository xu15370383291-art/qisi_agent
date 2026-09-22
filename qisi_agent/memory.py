from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .embeddings import HashEmbedding, cosine
from .models import utc_now


@dataclass(slots=True)
class MemoryItem:
    memory_id: str
    student_id: str
    content: str
    memory_type: str = "learning_event"
    course_id: str = ""
    knowledge_point_id: str = ""
    confidence: float = 0.8
    importance: float = 0.5
    occurred_at: str = field(default_factory=utc_now)
    source_message_id: str = ""
    version: int = 1
    status: str = "candidate"
    semantic_key: str = ""
    supersedes_memory_id: str = ""
    status_reason: str = ""
    last_reinforced_at: str = ""
    valid_until: str = ""
    archived_at: str = ""

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "student_id": self.student_id,
            "content": self.content,
            "memory_type": self.memory_type,
            "course_id": self.course_id,
            "knowledge_point_id": self.knowledge_point_id,
            "confidence": self.confidence,
            "importance": self.importance,
            "occurred_at": self.occurred_at,
            "source_message_id": self.source_message_id,
            "version": self.version,
            "status": self.status,
            "semantic_key": self.semantic_key,
            "supersedes_memory_id": self.supersedes_memory_id,
            "status_reason": self.status_reason,
            "last_reinforced_at": self.last_reinforced_at,
            "valid_until": self.valid_until,
            "archived_at": self.archived_at,
        }


class MemoryStore:
    def __init__(self, ttl_seconds: int = 3600, storage_path: str | Path | None = None):
        self.ttl_seconds = ttl_seconds
        self.storage_path = Path(storage_path) if storage_path else None
        self.short_term: dict[str, tuple[datetime, list[dict]]] = {}
        self.long_term: dict[str, MemoryItem] = {}
        self.mistakes: dict[str, dict] = {}
        self.practice_stats: dict[str, dict[str, dict[str, int]]] = {}
        self.memory_evidence: dict[str, list[dict[str, Any]]] = {}
        self.embedding = HashEmbedding()
        self._load()

    def write_mistake(self, student_id: str, *, prompt: str, student_answer: str = "",
                      correct_answer: str = "", explanation: str = "", knowledge_point_id: str = "",
                      source_message_id: str = "") -> dict:
        mistake_id = hashlib.sha1(f"{student_id}:{prompt}:{source_message_id}".encode()).hexdigest()[:16]
        item = {"mistake_id": mistake_id, "student_id": student_id, "prompt": prompt,
                "student_answer": student_answer, "correct_answer": correct_answer,
                "explanation": explanation, "knowledge_point_id": knowledge_point_id,
                "source_message_id": source_message_id, "status": "unreviewed", "created_at": utc_now()}
        if not hasattr(self, "mistakes"):
            self.mistakes = {}
        self.mistakes[mistake_id] = item
        self._persist()
        return item

    def list_mistakes(self, student_id: str, *, include_reviewed: bool = True) -> list[dict]:
        rows = [item for item in getattr(self, "mistakes", {}).values() if item["student_id"] == student_id]
        if not include_reviewed:
            rows = [item for item in rows if item["status"] != "reviewed"]
        return sorted(rows, key=lambda item: item["created_at"], reverse=True)

    def review_mistake(self, mistake_id: str, student_id: str) -> dict:
        item = getattr(self, "mistakes", {}).get(mistake_id)
        if not item or item["student_id"] != student_id:
            raise KeyError(mistake_id)
        item["status"] = "reviewed"
        item["reviewed_at"] = utc_now()
        self._persist()
        return item

    def _load(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self.long_term = {item["memory_id"]: MemoryItem(**item) for item in payload.get("long_term", [])}
            self.mistakes = {item["mistake_id"]: item for item in payload.get("mistakes", [])}
            self.practice_stats = payload.get("practice_stats", {})
            self.memory_evidence = payload.get("memory_evidence", {})
            for session_id, messages in payload.get("short_term", {}).items():
                self.short_term[session_id] = (datetime.now(timezone.utc), messages[-20:])
        except (OSError, ValueError, TypeError, KeyError):
            # A corrupt optional cache must not prevent the API from starting.
            self.long_term = {}
            self.mistakes = {}
            self.practice_stats = {}
            self.memory_evidence = {}
            self.short_term = {}

    def _persist(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "long_term": [item.to_dict() for item in self.long_term.values()],
            "mistakes": list(getattr(self, "mistakes", {}).values()),
            "practice_stats": self.practice_stats,
            "memory_evidence": self.memory_evidence,
            "short_term": {session: messages for session, (_, messages) in self.short_term.items()},
        }
        temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.storage_path)

    def append_message(self, session_id: str, role: str, content: str) -> None:
        now = datetime.now(timezone.utc)
        messages = self.short_term.setdefault(session_id, (now, []))[1]
        messages.append({"role": role, "content": content, "at": now.isoformat()})
        self.short_term[session_id] = (now, messages[-20:])
        self._persist()

    def get_messages(self, session_id: str) -> list[dict]:
        item = self.short_term.get(session_id)
        if not item:
            return []
        created, messages = item
        if datetime.now(timezone.utc) - created > timedelta(seconds=self.ttl_seconds):
            self.short_term.pop(session_id, None)
            return []
        return list(messages)

    def delete_session(self, session_id: str) -> None:
        self.short_term.pop(session_id, None)
        self._persist()

    def session_summary(self, session_id: str) -> dict:
        messages = self.get_messages(session_id)
        first_user = next((item["content"] for item in messages if item.get("role") == "user"), "新学习对话")
        last_at = messages[-1].get("at", "") if messages else ""
        return {"session_id": session_id, "title": first_user[:32],
                "message_count": len(messages), "updated_at": last_at}

    def write(self, student_id: str, content: str, *, course_id: str = "", knowledge_point_id: str = "",
              source_message_id: str = "", confidence: float = 0.8, importance: float = 0.5,
              memory_type: str = "learning_event", semantic_key: str = "",
              supersedes_memory_id: str = "", status_reason: str = "",
              initial_status: str = "candidate") -> MemoryItem:
        vector = self.embedding.embed(content)
        for item in self.long_term.values():
            if item.student_id == student_id and cosine(vector, self.embedding.embed(item.content)) >= 0.92:
                item.version += 1
                item.content = content
                item.status = "updated"
                item.confidence = confidence
                item.source_message_id = source_message_id or item.source_message_id
                item.importance = importance
                item.last_reinforced_at = utc_now()
                self._persist()
                return item
        digest = hashlib.sha1(f"{student_id}:{content}".encode()).hexdigest()[:16]
        item = MemoryItem(digest, student_id, content, memory_type=memory_type, course_id=course_id,
                          knowledge_point_id=knowledge_point_id, source_message_id=source_message_id,
                          confidence=confidence, importance=importance, semantic_key=semantic_key,
                          supersedes_memory_id=supersedes_memory_id, status_reason=status_reason,
                          status=initial_status)
        self.long_term[digest] = item
        self._persist()
        return item

    def record_memory_evidence(self, memory_id: str, source_kind: str, source_id: str,
                               evidence_role: str, payload: dict[str, Any]) -> None:
        if memory_id not in self.long_term:
            raise KeyError(memory_id)
        self.memory_evidence.setdefault(memory_id, []).append({
            "source_kind": source_kind, "source_id": source_id, "evidence_role": evidence_role,
            "payload": dict(payload), "created_at": utc_now(),
        })
        self._persist()

    def list_memory_evidence(self, memory_id: str) -> list[dict[str, Any]]:
        return list(self.memory_evidence.get(memory_id, []))

    def transition_memory(self, memory_id: str, status: str, *, reason: str = "",
                          supersedes_memory_id: str = "") -> MemoryItem:
        allowed = {"candidate", "active", "updated", "superseded", "stale", "archived", "conflict", "deleted"}
        if status not in allowed:
            raise ValueError("无效的记忆状态")
        item = self.long_term.get(memory_id)
        if item is None:
            raise KeyError(memory_id)
        item.status, item.status_reason = status, reason
        if supersedes_memory_id:
            item.supersedes_memory_id = supersedes_memory_id
        if status == "archived":
            item.archived_at = utc_now()
        self.record_memory_evidence(memory_id, "memory", supersedes_memory_id,
                                    "supersedes" if status == "superseded" else "contradicts" if status == "conflict" else "supports",
                                    {"reason": reason})
        self._persist()
        return item

    def recall(self, student_id: str, query: str, top_k: int = 5) -> list[MemoryItem]:
        query_vector = self.embedding.embed(query)
        rows = [(cosine(query_vector, self.embedding.embed(item.content)) * (0.5 + item.importance / 2), item)
                for item in self.long_term.values() if item.student_id == student_id and item.status in {"candidate", "active", "updated"}]
        rows.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in rows[:top_k]]

    def review(self, memory_id: str, action: str, content: str | None = None) -> MemoryItem:
        if memory_id not in self.long_term:
            raise KeyError(memory_id)
        item = self.long_term[memory_id]
        if action == "delete":
            item.status = "deleted"
        elif action == "archive":
            item.status = "archived"
            item.archived_at = utc_now()
        elif action == "stale":
            item.status = "stale"
        elif action in {"approve", "activate"}:
            item.status = "active"
        elif action == "correct":
            if not content or not content.strip():
                raise ValueError("correct 操作需要 content")
            item.content = content.strip()
            item.version += 1
            item.status = "updated"
        else:
            raise ValueError(f"未知审核操作：{action}")
        self._persist()
        return item

    def profile(self, student_id: str) -> dict:
        items = [item for item in self.long_term.values()
                 if item.student_id == student_id and item.status in {"candidate", "active", "updated"}]
        return {
            "student_id": student_id,
            "memory_count": len(items),
            "knowledge_points": sorted({item.knowledge_point_id for item in items if item.knowledge_point_id}),
            "recent_events": [item.content for item in sorted(items, key=lambda value: value.occurred_at, reverse=True)[:10]],
            "mastery": self.mastery(student_id),
        }

    def mastery(self, student_id: str) -> list[dict]:
        stats: dict[str, dict[str, int]] = {}
        for item in self.long_term.values():
            if item.student_id != student_id or item.status not in {"candidate", "active", "updated"} or not item.knowledge_point_id:
                continue
            row = stats.setdefault(item.knowledge_point_id, {"signals": 0, "mistakes": 0})
            row["signals"] += 1
        for item in getattr(self, "mistakes", {}).values():
            if item["student_id"] == student_id and item.get("status") != "reviewed":
                point = item.get("knowledge_point_id") or "待归类"
                row = stats.setdefault(point, {"signals": 0, "mistakes": 0})
                row["mistakes"] += 1
        for point, stats_row in self.practice_stats.get(student_id, {}).items():
            row = stats.setdefault(point, {"signals": 0, "mistakes": 0})
            row["attempts"] = stats_row.get("attempts", 0)
            row["correct"] = stats_row.get("correct", 0)
        result = []
        for point, row in stats.items():
            attempts = row.get("attempts", 0)
            accuracy = row.get("correct", 0) / attempts if attempts else 0.5
            score = max(0, min(100, round(accuracy * 70 + min(30, row["signals"] * 10) - row["mistakes"] * 12)))
            status = "薄弱" if score < 45 else "学习中" if score < 80 else "已掌握"
            result.append({"knowledge_point": point, "score": score, "status": status,
                           "signals": row["signals"], "mistakes": row["mistakes"]})
        return sorted(result, key=lambda item: (item["score"], item["knowledge_point"]))

    def record_practice_result(self, student_id: str, knowledge_point_id: str,
                               correct: bool) -> None:
        if not knowledge_point_id:
            return
        student = self.practice_stats.setdefault(student_id, {})
        stats = student.setdefault(knowledge_point_id, {"attempts": 0, "correct": 0})
        stats["attempts"] += 1
        stats["correct"] += int(correct)
        self._persist()

    def list_for_student(self, student_id: str, *, include_deleted: bool = False) -> list[MemoryItem]:
        items = [item for item in self.long_term.values() if item.student_id == student_id]
        if not include_deleted:
            items = [item for item in items if item.status != "deleted"]
        return sorted(items, key=lambda value: value.occurred_at, reverse=True)


def extract_learning_event(text: str) -> dict[str, str | float] | None:
    """阶段 2 的结构化抽取基线；接入 LLM 时保持此字段契约。"""
    if not re.search(r"错题|不会|掌握|复习|总是错|不懂", text):
        return None
    knowledge_point_id = ""
    if "分数" in text or "约分" in text:
        knowledge_point_id = "分数约分"
    elif "方程" in text:
        knowledge_point_id = "一元一次方程"
    return {"memory_type": "learning_event", "content": text.strip(), "confidence": 0.65,
            "importance": 0.7, "knowledge_point_id": knowledge_point_id,
            "occurred_at": utc_now(), "version": 1}


LEARNING_EVENT_TYPES = frozenset({"learning_event", "mastery_signal", "difficulty_signal", "study_preference"})
LEARNING_CLAIMS = frozenset({"neutral", "mastered", "needs_review"})


def _event_number(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


class LearningEventExtractor:
    """Extract one durable learning signal using a constrained LLM JSON contract.

    The extractor deliberately treats the model output as *proposed evidence*,
    not as a fact.  Strict local validation and the rule baseline make a failed
    or malformed model response harmless to the chat path.
    """

    def __init__(self, chat_service: Any | None = None):
        self.chat_service = chat_service

    @staticmethod
    def should_extract(text: str) -> bool:
        return bool(re.search(r"错题|不会|掌握|复习|总是错|不懂|薄弱|学会|做错|困难|容易", text))

    @staticmethod
    def _json_object(raw: str) -> dict[str, Any] | None:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return data if isinstance(data, dict) else None

    def _validate(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("relevant") is not True:
            return None
        content = str(payload.get("content", "")).strip()
        if not 6 <= len(content) <= 500:
            return None
        memory_type = str(payload.get("memory_type", "learning_event")).strip()
        if memory_type not in LEARNING_EVENT_TYPES:
            return None
        point = str(payload.get("knowledge_point_id", "")).strip()[:100]
        claim = str(payload.get("claim", "neutral")).strip()
        if claim not in LEARNING_CLAIMS:
            claim = "neutral"
        semantic_key = str(payload.get("semantic_key", "")).strip()[:160]
        if not semantic_key:
            semantic_key = ":".join(part for part in (point, memory_type, claim) if part)[:160]
        return {
            "memory_type": memory_type,
            "content": content,
            "confidence": _event_number(payload.get("confidence"), 0.65),
            "importance": _event_number(payload.get("importance"), 0.6),
            "knowledge_point_id": point,
            "claim": claim,
            "semantic_key": semantic_key,
            "occurred_at": utc_now(),
            "extraction_method": "llm_json_schema",
        }

    def extract(self, text: str, *, answer: str = "") -> dict[str, Any] | None:
        if not self.should_extract(text):
            return None
        if self.chat_service is not None:
            prompt = """从学生本轮对话中提取至多一条可长期保存的学习信号。不要推断未表达的事实。
只返回一个 JSON 对象，不要 Markdown：
{"relevant":true|false,"content":"简洁、可追溯的学生学习事实","memory_type":"learning_event|mastery_signal|difficulty_signal|study_preference","knowledge_point_id":"知识点或空字符串","claim":"neutral|mastered|needs_review","confidence":0到1,"importance":0到1,"semantic_key":"同一知识点和主张的稳定键"}
学生消息：\n""" + text.strip() + "\n助手回答（仅用于理解上下文，不能作为学生事实）：\n" + answer.strip()
            try:
                response = self.chat_service.complete([
                    {"role": "system", "content": "你是严格的教育数据结构化提取器。"},
                    {"role": "user", "content": prompt},
                ])
                event = self._validate(self._json_object(str(getattr(response, "content", ""))) or {})
                if event:
                    return event
            except Exception:
                # Answer delivery and durable storage must remain independent.
                pass
        fallback = extract_learning_event(text)
        if fallback is None:
            return None
        fallback = dict(fallback)
        fallback["claim"] = "needs_review" if any(word in text for word in ("不会", "错", "不懂", "复习", "薄弱")) else "neutral"
        fallback["semantic_key"] = ":".join(part for part in (
            str(fallback.get("knowledge_point_id", "")), str(fallback["memory_type"]), str(fallback["claim"])
        ) if part)
        fallback["extraction_method"] = "rule_fallback"
        return fallback


def _claim_from_text(text: str) -> str:
    normalized = text.lower()
    if any(word in normalized for word in ("掌握", "学会", "会做", "熟练")):
        return "mastered"
    if any(word in normalized for word in ("不会", "错误", "错", "不懂", "薄弱", "复习", "困难")):
        return "needs_review"
    return "neutral"


class MemoryConsolidator:
    """Merge structured learning events without destroying their history."""

    def __init__(self, store: Any):
        self.store = store

    @staticmethod
    def _same_topic(item: MemoryItem, event: dict[str, Any]) -> bool:
        key = str(event.get("semantic_key", ""))
        return bool(key and item.semantic_key == key) or (
            bool(event.get("knowledge_point_id"))
            and item.knowledge_point_id == event.get("knowledge_point_id")
            and item.memory_type == event.get("memory_type", "learning_event")
        )

    def persist(self, student_id: str, event: dict[str, Any], *, source_message_id: str) -> MemoryItem:
        content = str(event["content"]).strip()
        all_candidates = [item for item in self.store.list_for_student(student_id, include_deleted=True)
                          if item.status != "deleted" and self._same_topic(item, event)]
        # An exact archived record is deliberately eligible for reactivation;
        # unrelated archived records never take part in semantic replacement.
        exact = next((item for item in all_candidates if item.content == content), None)
        candidates = [item for item in all_candidates if item.status != "archived"]
        if exact is not None:
            reinforced = self.store.write(student_id, content, confidence=float(event["confidence"]),
                                          importance=float(event["importance"]), source_message_id=source_message_id,
                                          knowledge_point_id=str(event.get("knowledge_point_id", "")),
                                          memory_type=str(event.get("memory_type", "learning_event")),
                                          semantic_key=str(event.get("semantic_key", "")),
                                          status_reason="重复学习信号强化")
            self.store.record_memory_evidence(reinforced.memory_id, "learning_event", source_message_id,
                                              "reinforces", {"method": event.get("extraction_method", "unknown")})
            return reinforced

        claim = str(event.get("claim") or _claim_from_text(content))
        previous = next((item for item in candidates if item.status in {"candidate", "active", "updated"}), None)
        explicit_conflict = bool(event.get("conflicts_with_prior", False))
        if previous is not None:
            if explicit_conflict:
                self.store.transition_memory(previous.memory_id, "conflict", reason="新旧学习断言互相矛盾")
                initial_status, reason = "conflict", "与已有记忆冲突，等待后续证据确认"
            else:
                self.store.transition_memory(previous.memory_id, "superseded", reason="较新的学习信号已替代")
                initial_status, reason = ("active" if float(event["confidence"]) >= 0.75 else "candidate"), "较新学习信号"
        else:
            initial_status, reason = ("active" if float(event["confidence"]) >= 0.75 else "candidate"), "新学习信号"

        item = self.store.write(student_id, content, confidence=float(event["confidence"]),
                                importance=float(event["importance"]), source_message_id=source_message_id,
                                knowledge_point_id=str(event.get("knowledge_point_id", "")),
                                memory_type=str(event.get("memory_type", "learning_event")),
                                semantic_key=str(event.get("semantic_key", "")),
                                supersedes_memory_id=previous.memory_id if previous and not explicit_conflict else "",
                                status_reason=reason, initial_status=initial_status)
        # The preceding transition did not know the new stable ID.  Record the
        # bidirectional lineage in evidence even for storage backends that do
        # not support patching a foreign-key column after creation.
        if previous is not None:
            self.store.record_memory_evidence(item.memory_id, "memory", previous.memory_id,
                                              "contradicts" if explicit_conflict else "supersedes",
                                              {"claim": claim})
        self.store.record_memory_evidence(item.memory_id, "learning_event", source_message_id, "extraction", {
            "method": event.get("extraction_method", "unknown"), "claim": claim,
        })
        return item


MEMORY_HALF_LIFE_DAYS = {
    "difficulty_signal": 45.0,
    "learning_event": 75.0,
    "mastery_signal": 120.0,
    "study_preference": 240.0,
}


def memory_freshness(memory_type: str, occurred_at: str, last_reinforced_at: str = "") -> float:
    """Return a non-negative exponential freshness score for retrieval only."""
    value = last_reinforced_at or occurred_at
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0.5
    age_days = max(0.0, (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 86400)
    half_life = MEMORY_HALF_LIFE_DAYS.get(memory_type, MEMORY_HALF_LIFE_DAYS["learning_event"])
    return math.pow(0.5, age_days / half_life)


class MemoryDecayService:
    """Apply lifecycle transitions without deleting historical learning evidence."""

    def __init__(self, store: Any):
        self.store = store

    def apply(self) -> dict[str, int]:
        counts = {"stale": 0, "archived": 0, "checked": 0}
        for item in self.store.list_for_student_all() if hasattr(self.store, "list_for_student_all") else list(self.store.long_term.values()):
            if item.status not in {"candidate", "active", "updated", "stale"}:
                continue
            counts["checked"] += 1
            freshness = memory_freshness(item.memory_type, item.occurred_at, item.last_reinforced_at)
            if freshness <= 0.0625 and item.status != "archived":  # four half-lives
                self.store.transition_memory(item.memory_id, "archived", reason="长期未被新的学习证据强化")
                counts["archived"] += 1
            elif freshness <= 0.25 and item.status != "stale":  # two half-lives
                self.store.transition_memory(item.memory_id, "stale", reason="学习信号已随时间衰减，等待重新确认")
                counts["stale"] += 1
        return counts
