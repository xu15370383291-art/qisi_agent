from __future__ import annotations

import hashlib
import csv
import json
import re
from pathlib import Path

from .models import Chunk


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_POINT = re.compile(r"(?:知识点|考点)[:：]\s*([^\n]+)")


def _infer_grade(path: Path, text: str = "") -> tuple[str, str]:
    """Infer a stable grade id from the filename or document headings."""
    haystack = f"{path.stem}\n{text[:1000]}".lower()
    patterns = (("七", "grade7", "七年级"), ("八", "grade8", "八年级"),
                ("九", "grade9", "九年级"))
    for chinese, grade_id, label in patterns:
        if f"{chinese}年级" in haystack:
            return grade_id, label
    match = re.search(r"(?:grade|g)[ _-]*([789])", haystack)
    if match:
        number = match.group(1)
        return f"grade{number}", f"{number}年级"
    return "", ""


def _document_id(path: Path) -> str:
    # Content-based IDs stay stable whether the index is built from a relative or absolute root.
    payload = path.name.encode("utf-8") + b"\0" + path.read_bytes()
    return hashlib.sha1(payload).hexdigest()[:12]


def parse_markdown(path: Path, max_chars: int = 700) -> list[Chunk]:
    """按标题和段落边界切分，保留来源和知识点元数据。"""
    text = path.read_text(encoding="utf-8")
    document_id = _document_id(path)
    grade_id, grade = _infer_grade(path, text)
    title = path.stem
    chapter = ""
    points: list[str] = []
    sections: list[tuple[str, str, str, list[str]]] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            sections.append((title, chapter, "\n".join(buffer).strip(), list(points)))
            buffer.clear()

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            heading = match.group(2)
            if len(match.group(1)) <= 2:
                chapter = heading
            title = heading
            continue
        point = _POINT.search(line)
        if point:
            points = [p.strip() for p in re.split(r"[,，、]", point.group(1)) if p.strip()]
        if line.strip():
            buffer.append(line.strip())
    flush()

    chunks: list[Chunk] = []
    index = 0
    for section_title, section_chapter, content, section_points in sections:
        words = content.split()
        if len(content) <= max_chars:
            parts = [content]
        elif len(words) <= 1:
            parts = [content[start:start + max_chars] for start in range(0, len(content), max_chars)]
        else:
            parts, current = [], ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and len(candidate) > max_chars:
                    parts.append(current)
                    current = word
                else:
                    current = candidate
            if current:
                parts.append(current)
        for part in parts:
            index += 1
            chunks.append(
                Chunk(
                    document_id=document_id,
                    chunk_id=f"{document_id}-{index:04d}",
                    text=part,
                    title=section_title,
                    chapter_id=section_chapter,
                    source_path=str(path.as_posix()),
                    knowledge_point_ids=section_points,
                    metadata={"format": path.suffix.lstrip(".") or "text",
                              "grade_id": grade_id, "grade": grade},
                )
            )
    return chunks


def _question_chunk(path: Path, row: dict, index: int) -> Chunk:
    question_id = str(row.get("question_id") or row.get("id") or index)
    stem = str(row.get("question") or row.get("stem") or row.get("题干") or "").strip()
    if not stem:
        raise ValueError(f"题库记录缺少题干：{path} #{index}")
    answer = row.get("answer") or row.get("答案") or row.get("correct_answer") or ""
    explanation = str(row.get("explanation") or row.get("解析") or "").strip()
    knowledge = str(row.get("knowledge_point") or row.get("knowledge_points") or row.get("知识点") or "")
    chapter = str(row.get("chapter") or row.get("chapter_id") or row.get("章节") or "")
    difficulty = str(row.get("difficulty") or row.get("难度") or "")
    raw_options = row.get("options") or row.get("choices") or row.get("选项") or []
    if isinstance(raw_options, dict):
        options = [str(value).strip() for _, value in sorted(raw_options.items())]
    elif isinstance(raw_options, str):
        options = [item.strip() for item in re.split(r"[|；;\n]", raw_options) if item.strip()]
    else:
        options = [str(item).strip() for item in raw_options if str(item).strip()]
    answer_index = next((row.get(key) for key in ("answer_index", "correct_index", "正确选项")
                         if row.get(key) not in (None, "")), None)
    if answer_index not in (None, ""):
        try:
            answer_index = int(answer_index)
            if answer_index >= 1 and answer_index <= len(options):
                answer_index -= 1
        except (TypeError, ValueError):
            answer_index = None
    else:
        answer_index = None
    answer_text = str(answer).strip()
    if answer_index is None and answer_text and options:
        normalized = answer_text.upper().rstrip(".")
        if len(normalized) == 1 and normalized in "ABCDEFGHIJ":
            candidate = ord(normalized) - ord("A")
            answer_index = candidate if candidate < len(options) else None
        if answer_index is None:
            answer_index = next((i for i, option in enumerate(options) if option == answer_text), None)
    text = f"题目：{stem}"
    if options:
        text += "\n选项：" + "；".join(options)
    if answer:
        text += f"\n答案：{answer}"
    if explanation:
        text += f"\n解析：{explanation}"
    if difficulty:
        text += f"\n难度：{difficulty}"
    points = [item.strip() for item in re.split(r"[,，、;；]", knowledge) if item.strip()]
    document_id = _document_id(path)
    grade_id, grade = _infer_grade(path, chapter)
    metadata = {"format": path.suffix.lstrip("."), "question_id": question_id,
                "difficulty": difficulty, "grade_id": grade_id, "grade": grade,
                "question_stem": stem, "options": options,
                "answer": answer_text, "answer_index": answer_index,
                "explanation": explanation}
    return Chunk(document_id=document_id, chunk_id=f"{document_id}-q{index:04d}", text=text,
                 title=f"题目 {question_id}", chapter_id=chapter, source_path=str(path.as_posix()),
                 knowledge_point_ids=points, metadata=metadata)


def parse_question_bank(path: Path) -> list[Chunk]:
    """导入 JSON/JSONL/CSV 题库，统一映射到可追踪 Chunk。"""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("questions", payload.get("items", []))
    if not isinstance(rows, list):
        raise ValueError(f"题库文件必须是记录列表：{path}")
    return [_question_chunk(path, row, index) for index, row in enumerate(rows, 1)]


def load_corpus(source: str | Path) -> list[Chunk]:
    root = Path(source)
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown", ".txt"}:
            chunks.extend(parse_markdown(path))
        elif suffix in {".json", ".jsonl", ".csv"}:
            chunks.extend(parse_question_bank(path))
    if not chunks:
        raise ValueError(f"知识库目录为空：{root}")
    return chunks
