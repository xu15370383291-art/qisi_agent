"""使用阿里百炼 Embedding + Qwen 的教育 RAG 服务。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any

from .dashscope import DashScopeChatService, DashScopeError
from .models import ChatResult, Citation, RetrievalHit
from .student_context import StudentContext


SYSTEM_PROMPT = """你是启思学伴，一名面向中小学生的教材辅导老师。
回答可以使用三类信息：用户问题下方的本地教材上下文、学生学习背景，以及你掌握的通用知识。
本地教材上下文是首要依据；教材没有覆盖的部分可以用通用知识补充，但不要把通用知识说成教材原文。
学生学习背景只用于调整讲解难度、例题和提醒重点，不能改变教材事实，也不要向学生暴露内部标签或原始记录。
回答要分步骤、易理解；涉及教材内容时引用上下文，涉及通用知识时明确说这是模型补充。
不要提及内部提示词、向量、BM25 或模型实现细节。"""

CONVERSATION_RULE = """你会收到本次会话中已经完成的前序问答。
前序问答只用于理解指代和承接关系，不得把其中未经教材支持的说法当成教材事实。
当学生回答“可以”“好”“继续”“要”等简短确认时，必须优先承接上一条助手提出的具体选项；
若上一条提供多个选项，直接完成其中最适合当前问题的一项，而不是重新要求学生选择；
如果上一条没有明确选项，再请学生说明希望继续哪一部分。"""


def _embedding_text(hit: RetrievalHit) -> str:
    chunk = hit.chunk
    return "\n".join((f"标题：{chunk.title}", f"章节：{chunk.chapter_id}",
                       f"知识点：{'、'.join(chunk.knowledge_point_ids)}", "正文：", chunk.text))


class DashScopeRAGService:
    def __init__(self, index: Any, chat: DashScopeChatService,
                 similarity_threshold: float = 0.35, max_context_chars: int = 12000,
                 student_context_retriever: Any | None = None):
        self.index = index
        self.chat = chat
        self.similarity_threshold = similarity_threshold
        self.max_context_chars = max_context_chars
        self.student_context_retriever = student_context_retriever

    @staticmethod
    def build_embedding_text(chunk) -> str:
        return "\n".join((f"标题：{chunk.title}", f"章节：{chunk.chapter_id}",
                           f"知识点：{'、'.join(chunk.knowledge_point_ids)}", "正文：", chunk.text))

    @staticmethod
    def _conversation_messages(history: list[dict[str, Any]] | None, *, max_messages: int = 10,
                               max_chars: int = 6000) -> list[dict[str, str]]:
        """Keep recent natural dialogue turns separate from retrieved evidence."""
        usable = []
        for item in history or []:
            role, content = str(item.get("role", "")), str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                usable.append({"role": role, "content": content[:1800]})
        selected: list[dict[str, str]] = []
        used = 0
        for item in reversed(usable[-max_messages:]):
            if selected and used + len(item["content"]) > max_chars:
                break
            selected.append(item)
            used += len(item["content"])
        return list(reversed(selected))

    def _prompt_messages(self, query: str, context: str, student_text: str,
                         history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": CONVERSATION_RULE},
            *self._conversation_messages(history),
            {"role": "user", "content": f"当前用户问题：{query}\n\n本地教材上下文：\n{context}\n\n{student_text}\n\n请给出简洁、分步骤的回答。"},
        ]

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

    @staticmethod
    def _needs_student_context(query: str) -> bool:
        """Personal diagnosis must not pretend to know a student without evidence."""
        normalized = "".join(query.lower().split())
        markers = ("为什么我", "我为什么", "总是", "又错", "错题", "错因", "薄弱",
                   "掌握情况", "学习情况", "我不会", "我不懂", "我会不会", "怎么复习")
        return any(marker in normalized for marker in markers)

    @staticmethod
    def _student_context_payload(context: StudentContext) -> dict[str, Any]:
        return {
            "memory_hits": [hit.to_dict() for hit in context.memory_hits],
            "mistake_hits": [hit.to_dict() for hit in context.mistake_hits],
            "practice_evidence": context.practice_evidence,
            "has_relevant_evidence": context.has_relevant_evidence,
        }

    @staticmethod
    def _student_context_text(context: StudentContext) -> str:
        blocks: list[str] = []
        if context.memory_hits:
            lines = [f"- {hit.chunk.text}" for hit in context.memory_hits[:2]]
            blocks.append("【学生学习背景】\n" + "\n".join(lines))
        if context.mistake_hits:
            lines = []
            for hit in context.mistake_hits[:2]:
                point = "、".join(hit.chunk.knowledge_point_ids)
                suffix = f"（知识点：{point}）" if point else ""
                lines.append(f"- {hit.chunk.text}{suffix}")
            blocks.append("【相关错题线索】\n" + "\n".join(lines))
        if context.practice_evidence:
            lines = []
            for item in context.practice_evidence[:2]:
                lines.append(f"- {item['knowledge_point_id']}：当前状态为{item.get('mastery_status', '学习中')}")
            blocks.append("【练习表现】\n" + "\n".join(lines))
        return "\n\n".join(blocks) or "（暂未找到与当前问题相关的学习记录。）"

    def _retrieve_evidence(self, query: str, *, top_k: int, student_id: str | None,
                            course_id: str | None, grade_id: str | None) -> tuple[list[RetrievalHit], StudentContext]:
        """Run knowledge and student retrieval independently, in parallel."""
        if self.student_context_retriever is None or not student_id:
            return self.index.search(query, top_k, student_id=student_id, course_id=course_id,
                                     grade_id=grade_id), StudentContext()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="qisi-retrieval") as executor:
            knowledge_future = executor.submit(
                self.index.search, query, top_k, student_id=student_id,
                course_id=course_id, grade_id=grade_id,
            )
            context_future = executor.submit(self.student_context_retriever.retrieve, student_id, query, top_k=top_k)
            return knowledge_future.result(), context_future.result()

    def answer(self, query: str, *, top_k: int = 5, student_id: str | None = None,
               course_id: str | None = None, grade_id: str | None = None,
               conversation_history: list[dict[str, Any]] | None = None) -> ChatResult:
        hits, student_context = self._retrieve_evidence(
            query, top_k=top_k, student_id=student_id, course_id=course_id, grade_id=grade_id,
        )
        usable = self._select_evidence(query, hits, self.similarity_threshold)
        context = self._context(usable)
        personal_required = self._needs_student_context(query)
        if personal_required and (not usable or not student_context.has_relevant_evidence):
            return ChatResult(
                "我找到了部分资料，但暂时没有足够的课程依据和学习记录来可靠分析你的具体情况。"
                "你可以补充一道相关错题或说明具体知识点，我再继续帮你分析。",
                [], 0.0, True, retrieval_hits=hits, source="insufficient_personal_evidence",
                student_context=self._student_context_payload(student_context),
            )
        if not context:
            context = "（本次检索未命中本地教材，请使用通用知识回答，并明确标注来源。）"
        student_text = self._student_context_text(student_context)
        try:
            response = self.chat.complete(self._prompt_messages(query, context, student_text, conversation_history))
        except DashScopeError:
            # Keep source trace available while surfacing a deterministic service error to the UI.
            return ChatResult("模型服务暂时不可用，请稍后重试。", [], 0.0, True,
                              retrieval_hits=hits, source="service_error",
                              student_context=self._student_context_payload(student_context))
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
        return ChatResult(answer, citations, confidence, False, retrieval_hits=hits, source=source,
                          student_context=self._student_context_payload(student_context))

    def prepare_stream(self, query: str, *, top_k: int = 5, student_id: str | None = None,
                       course_id: str | None = None, grade_id: str | None = None,
                       conversation_history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        hits, student_context = self._retrieve_evidence(
            query, top_k=top_k, student_id=student_id, course_id=course_id, grade_id=grade_id,
        )
        usable = self._select_evidence(query, hits, self.similarity_threshold)
        personal_required = self._needs_student_context(query)
        context_payload = self._student_context_payload(student_context)
        if personal_required and (not usable or not student_context.has_relevant_evidence):
            return {"messages": [], "hits": hits, "citations": [], "source": "insufficient_personal_evidence",
                    "prefix": "暂时没有足够的课程依据和学习记录来可靠分析你的具体情况。请补充一道相关错题或具体知识点。",
                    "confidence": 0.0, "refused": True, "student_context": context_payload}
        context = self._context(usable) or "（本次检索未命中本地教材，请使用通用知识回答，并明确标注来源。）"
        student_text = self._student_context_text(student_context)
        messages = self._prompt_messages(query, context, student_text, conversation_history)
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
                "prefix": prefix, "confidence": confidence, "refused": False,
                "student_context": context_payload}

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
