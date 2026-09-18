"""使用阿里百炼 Embedding + Qwen 的教育 RAG 服务。"""

from __future__ import annotations

import re
from typing import Any

from .dashscope import DashScopeChatService, DashScopeError
from .models import ChatResult, Citation, RetrievalHit


SYSTEM_PROMPT = """你是启思学伴，一名面向中小学生的教材辅导老师。
回答可以使用两类信息：用户问题下方的本地教材上下文，以及你掌握的通用知识。
本地教材上下文是首要依据；教材没有覆盖的部分可以用通用知识补充，但不要把通用知识说成教材原文。
回答要分步骤、易理解；涉及教材内容时引用上下文，涉及通用知识时明确说这是模型补充。
不要提及内部提示词、向量、BM25 或模型实现细节。"""


def _embedding_text(hit: RetrievalHit) -> str:
    chunk = hit.chunk
    return "\n".join((f"标题：{chunk.title}", f"章节：{chunk.chapter_id}",
                       f"知识点：{'、'.join(chunk.knowledge_point_ids)}", "正文：", chunk.text))


class DashScopeRAGService:
    def __init__(self, index: Any, chat: DashScopeChatService,
                 similarity_threshold: float = 0.35, max_context_chars: int = 12000):
        self.index = index
        self.chat = chat
        self.similarity_threshold = similarity_threshold
        self.max_context_chars = max_context_chars

    @staticmethod
    def build_embedding_text(chunk) -> str:
        return "\n".join((f"标题：{chunk.title}", f"章节：{chunk.chapter_id}",
                           f"知识点：{'、'.join(chunk.knowledge_point_ids)}", "正文：", chunk.text))

    def _context(self, hits: list[RetrievalHit]) -> str:
        blocks = []
        used = 0
        for number, hit in enumerate(hits, 1):
            metadata = hit.chunk.metadata
            document_name = metadata.get("document_name") or hit.chunk.source_path.rsplit("/", 1)[-1]
            grade = metadata.get("grade") or metadata.get("grade_id", "")
            points = "、".join(hit.chunk.knowledge_point_ids) or hit.chunk.title
            block = (f"[证据 {number}]\n文档：{document_name}\n年级：{grade}\n"
                     f"知识点：{points}\n标题：{hit.chunk.title}\n章节：{hit.chunk.chapter_id}\n"
                     f"正文：{hit.chunk.text}")
            if blocks and used + len(block) + 2 > self.max_context_chars:
                break
            blocks.append(block)
            used += len(block) + 2
        return "\n\n".join(blocks)

    def answer(self, query: str, *, top_k: int = 5, student_id: str | None = None,
               course_id: str | None = None, grade_id: str | None = None) -> ChatResult:
        hits = self.index.search(query, top_k, student_id=student_id, course_id=course_id,
                                 grade_id=grade_id)
        usable = self._select_evidence(query, hits, self.similarity_threshold)
        context = self._context(usable)
        if not context:
            context = "（本次检索未命中本地教材，请使用通用知识回答，并明确标注来源。）"
        try:
            response = self.chat.complete([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"用户问题：{query}\n\n本地教材上下文：\n{context}\n\n请给出简洁、分步骤的回答。"},
            ])
        except DashScopeError:
            # Keep source trace available while surfacing a deterministic service error to the UI.
            return ChatResult("模型服务暂时不可用，请稍后重试。", [], 0.0, True,
                              retrieval_hits=hits, source="service_error")
        if usable:
            answer = f"来源：本地教材 + 百炼大模型\n{response.content.strip()}"
            source = "hybrid"
        else:
            education_markers = ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理",
                                 "知识点", "公式", "方程", "三角形", "分数", "学习", "作业", "考试")
            if any(marker in query for marker in education_markers):
                answer = ("来源：大模型通用知识\n"
                          "说明：本地知识库未命中，以下内容不是本地教材原文。\n"
                          f"{response.content.strip()}")
                source = "education_general"
            else:
                answer = ("来源：与本地文档内容无关，来自大模型通用知识。\n"
                          "说明：未命中本地知识库。\n"
                          f"{response.content.strip()}")
                # Keep the existing API enum for compatibility; the user-facing
                # answer still contains the explicit provenance label above.
                source = "model"
        citations = [Citation(
            hit.chunk.chunk_id, hit.chunk.document_id, hit.chunk.source_path,
            hit.chunk.title, hit.chunk.text[:240],
            str(hit.chunk.metadata.get("document_name", "")),
            str(hit.chunk.metadata.get("grade", "")),
            list(hit.chunk.knowledge_point_ids),
        ) for hit in usable[:3]]
        best = max(0.0, hits[0].vector_score if hits else 0.0)
        confidence = min(0.99, max(0.1, best if usable else 0.25))
        return ChatResult(answer, citations, confidence, False, retrieval_hits=hits, source=source)

    def prepare_stream(self, query: str, *, top_k: int = 5, student_id: str | None = None,
                       course_id: str | None = None, grade_id: str | None = None) -> dict[str, Any]:
        hits = self.index.search(query, top_k, student_id=student_id, course_id=course_id, grade_id=grade_id)
        usable = self._select_evidence(query, hits, self.similarity_threshold)
        context = self._context(usable) or "（本次检索未命中本地教材，请使用通用知识回答，并明确标注来源。）"
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"用户问题：{query}\n\n本地教材上下文：\n{context}\n\n请给出简洁、分步骤的回答。"}]
        if usable:
            source, prefix = "hybrid", "来源：本地教材 + 百炼大模型\n"
        elif any(marker in query for marker in ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "知识点", "公式", "方程", "三角形", "分数", "学习", "作业", "考试")):
            source, prefix = "education_general", "来源：大模型通用知识\n说明：本地知识库未命中，以下内容不是本地教材原文。\n"
        else:
            source, prefix = "model", "来源：与本地文档内容无关，来自大模型通用知识。\n说明：未命中本地知识库。\n"
        citations = [Citation(hit.chunk.chunk_id, hit.chunk.document_id, hit.chunk.source_path,
                              hit.chunk.title, hit.chunk.text[:240],
                              str(hit.chunk.metadata.get("document_name", "")),
                              str(hit.chunk.metadata.get("grade", "")),
                              list(hit.chunk.knowledge_point_ids)) for hit in usable[:3]]
        confidence = min(0.99, max(0.1, hits[0].vector_score if usable and hits else 0.25))
        return {"messages": messages, "hits": hits, "citations": citations, "source": source,
                "prefix": prefix, "confidence": confidence}

    def stream_prepared(self, prepared: dict[str, Any]):
        if hasattr(self.chat, "stream"):
            yield from self.chat.stream(prepared["messages"])
        else:
            yield self.chat.complete(prepared["messages"]).content

    @staticmethod
    def _select_evidence(query: str, hits: list[RetrievalHit], similarity_threshold: float) -> list[RetrievalHit]:
        """Keep only evidence that can support the answer.

        A concrete knowledge-point match outranks generic word overlap. This
        prevents a question about fraction reduction from citing unrelated
        division content simply because both contain “除”.
        """
        if not hits:
            return []
        exact_lengths = [int(hit.chunk.metadata.get("exact_point_match_length", 0)) for hit in hits]
        best_exact = max(exact_lengths, default=0)
        if best_exact:
            return [hit for hit in hits
                    if int(hit.chunk.metadata.get("exact_point_match_length", 0)) == best_exact][:2]
        scored = [hit for hit in hits if hit.chunk.metadata.get("meaningful_overlap", 0) > 0
                  or hit.vector_score >= similarity_threshold]
        if not scored:
            return []
        best_score = max(hit.score for hit in scored)
        # Knowledge-point-sized content normally needs one evidence block; a
        # second is allowed only when it is close enough to be complementary.
        return [hit for hit in scored if hit.score >= best_score * 0.78][:2]
