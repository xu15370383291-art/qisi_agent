from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        }


class MemoryStore:
    def __init__(self, ttl_seconds: int = 3600, storage_path: str | Path | None = None):
        self.ttl_seconds = ttl_seconds
        self.storage_path = Path(storage_path) if storage_path else None
        self.short_term: dict[str, tuple[datetime, list[dict]]] = {}
        self.long_term: dict[str, MemoryItem] = {}
        self.mistakes: dict[str, dict] = {}
        self.practice_stats: dict[str, dict[str, dict[str, int]]] = {}
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
            for session_id, messages in payload.get("short_term", {}).items():
                self.short_term[session_id] = (datetime.now(timezone.utc), messages[-20:])
        except (OSError, ValueError, TypeError, KeyError):
            # A corrupt optional cache must not prevent the API from starting.
            self.long_term = {}
            self.mistakes = {}
            self.practice_stats = {}
            self.short_term = {}

    def _persist(self) -> None:
        if not self.storage_path:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "long_term": [item.to_dict() for item in self.long_term.values()],
            "mistakes": list(getattr(self, "mistakes", {}).values()),
            "practice_stats": self.practice_stats,
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
              source_message_id: str = "", confidence: float = 0.8, importance: float = 0.5) -> MemoryItem:
        vector = self.embedding.embed(content)
        for item in self.long_term.values():
            if item.student_id == student_id and cosine(vector, self.embedding.embed(item.content)) >= 0.92:
                item.version += 1
                item.content = content
                item.status = "updated"
                item.confidence = confidence
                item.source_message_id = source_message_id or item.source_message_id
                item.importance = importance
                self._persist()
                return item
        digest = hashlib.sha1(f"{student_id}:{content}".encode()).hexdigest()[:16]
        item = MemoryItem(digest, student_id, content, course_id=course_id,
                          knowledge_point_id=knowledge_point_id, source_message_id=source_message_id,
                          confidence=confidence, importance=importance)
        self.long_term[digest] = item
        self._persist()
        return item

    def recall(self, student_id: str, query: str, top_k: int = 5) -> list[MemoryItem]:
        query_vector = self.embedding.embed(query)
        rows = [(cosine(query_vector, self.embedding.embed(item.content)) * (0.5 + item.importance / 2), item)
                for item in self.long_term.values() if item.student_id == student_id and item.status != "deleted"]
        rows.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in rows[:top_k]]

    def review(self, memory_id: str, action: str, content: str | None = None) -> MemoryItem:
        if memory_id not in self.long_term:
            raise KeyError(memory_id)
        item = self.long_term[memory_id]
        if action == "delete":
            item.status = "deleted"
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
                 if item.student_id == student_id and item.status != "deleted"]
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
            if item.student_id != student_id or item.status == "deleted" or not item.knowledge_point_id:
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
