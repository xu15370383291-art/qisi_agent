from __future__ import annotations

import re
from typing import Any

import psycopg
from rank_bm25 import BM25Okapi

from .embeddings import meaningful_overlap, tokenize
from .models import Chunk, RetrievalHit
from .reranker import Reranker, cross_encoder_from_env


class PostgresHybridIndex:
    """PostgreSQL-backed hybrid retriever.

    PostgreSQL owns documents, points and pgvector candidates. BM25 is built in
    application memory from the same rows, then both candidate sets are fused
    and reranked. The public interface is consumed by the API and RAG service.
    """

    def __init__(self, database_url: str, embedder: Any, *, reranker: Reranker | None = None):
        self.database_url = database_url
        self.embedding = embedder
        self.embedding_model = getattr(embedder, "model", "unknown")
        self.dimensions = int(getattr(embedder, "dimensions", getattr(embedder.config, "embedding_dimensions", 0)))
        self.reranker = reranker or cross_encoder_from_env()
        self.chunks, self.vectors = self._load_rows()
        self._chunk_index = {chunk.chunk_id: index for index, chunk in enumerate(self.chunks)}
        self._tokens = [self._tokens_for(chunk) for chunk in self.chunks]
        self._bm25 = BM25Okapi(self._tokens) if self._tokens else None
        self.vectors_persisted = bool(self.vectors)

    @staticmethod
    def _tokens_for(chunk: Chunk) -> list[str]:
        text = " ".join((chunk.title, chunk.chapter_id, chunk.text,
                         *chunk.knowledge_point_ids,
                         str(chunk.metadata.get("grade_id", "")),
                         str(chunk.metadata.get("grade", ""))))
        return tokenize(text)

    def _load_rows(self) -> tuple[list[Chunk], list[list[float]]]:
        sql = """
          SELECT c.content_id, c.document_id, c.title, c.chapter_id, c.content, c.metadata,
                 d.document_name, d.grade_id, d.grade_name, d.source_path,
                 COALESCE(array_agg(p.point_name ORDER BY p.ordinal)
                          FILTER (WHERE p.point_name IS NOT NULL), ARRAY[]::text[]) AS points,
                 e.embedding::text
          FROM knowledge_contents c
          JOIN knowledge_documents d USING(document_id)
          LEFT JOIN knowledge_points p USING(content_id)
          LEFT JOIN knowledge_content_embeddings e USING(content_id)
          WHERE d.status = 'published'
          GROUP BY c.content_id, c.document_id, c.title, c.chapter_id, c.content, c.metadata,
                   d.document_name, d.grade_id, d.grade_name, d.source_path, e.embedding
          ORDER BY c.content_id
        """
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        chunks: list[Chunk] = []
        vectors: list[list[float]] = []
        missing = 0
        for content_id, document_id, title, chapter, content, metadata, document_name, grade_id, grade_name, source_path, points, vector_text in rows:
            if not vector_text:
                missing += 1
                continue
            vector = [float(value) for value in vector_text.strip("[]").split(",") if value.strip()]
            if len(vector) != self.dimensions:
                raise ValueError(f"{content_id} 向量维度错误：期望 {self.dimensions}，实际 {len(vector)}")
            chunks.append(Chunk(document_id=document_id, chunk_id=content_id, text=content,
                                title=title or "", chapter_id=chapter or "", source_path=source_path,
                                knowledge_point_ids=list(points or []),
                                metadata={**(metadata or {}), "format": "postgres", "document_name": document_name,
                                          "grade_id": grade_id, "grade": grade_name}))
            vectors.append(vector)
        if missing:
            raise ValueError(f"有 {missing} 条正文尚未生成向量，请先运行 pg-embed")
        if not chunks:
            raise ValueError("PostgreSQL 知识库为空，请先运行 pg-import")
        return chunks, vectors

    def _route(self, query: str, query_tokens: list[str]) -> dict[str, float | int | str]:
        compact = "".join(query.lower().split())
        punctuation_free = re.sub(r"[？?！!，,。；;：:]", "", compact)
        exact_point_lengths = [
            len("".join(point.lower().split()))
            for chunk in self.chunks
            for point in chunk.knowledge_point_ids
            if point and (
                "".join(point.lower().split()) in punctuation_free
                or self._phrase_in_query("".join(point.lower().split()), punctuation_free)
            )
        ]
        exact_points = len(exact_point_lengths)
        specific_point_length = max(exact_point_lengths, default=0)
        exact_titles = sum(1 for chunk in self.chunks if chunk.title and
                           "".join(chunk.title.lower().split()) in punctuation_free)
        vocab = set(token for row in self._tokens for token in row)
        lexical_overlap = sum(1 for token in set(query_tokens) if token in vocab)
        overlap_ratio = lexical_overlap / max(1, len(set(query_tokens)))
        semantic_markers = ("怎么", "怎样", "如何", "为什么", "为何", "应该", "能否", "可以吗",
                            "区别", "含义", "解释", "理解", "意思", "证明", "判断")
        semantic_marker = any(marker in query for marker in semantic_markers)
        keyword_strength = min(1.0, 0.45 * overlap_ratio +
                                0.35 * min(1.0, specific_point_length / 6) +
                                0.20 * min(1, exact_titles))
        semantic_strength = min(1.0, (0.45 if semantic_marker else 0.0) +
                                0.55 * (1.0 - overlap_ratio))
        if specific_point_length >= 4:
            keyword_strength = max(keyword_strength, 0.9)
            semantic_strength *= 0.55
        if keyword_strength >= semantic_strength + 0.18:
            mode, bm25_weight, vector_weight, bm25_pool, vector_pool = "keyword", 0.70, 0.30, 8, 3
        elif semantic_strength >= keyword_strength + 0.18:
            mode, bm25_weight, vector_weight, bm25_pool, vector_pool = "semantic", 0.30, 0.70, 3, 8
        else:
            mode, bm25_weight, vector_weight, bm25_pool, vector_pool = "balanced", 0.50, 0.50, 5, 5
        return {"mode": mode, "bm25_weight": bm25_weight, "vector_weight": vector_weight,
                "bm25_pool": bm25_pool, "vector_pool": vector_pool,
                "keyword_strength": round(keyword_strength, 4),
                "semantic_strength": round(semantic_strength, 4),
                "exact_points": exact_points, "exact_titles": exact_titles}

    @staticmethod
    def _phrase_in_query(phrase: str, query: str) -> bool:
        if not phrase:
            return False
        fillers = set("的之与及和或是为有")
        position = 0
        for char in phrase:
            while position < len(query) and query[position] in fillers:
                position += 1
            position = query.find(char, position)
            if position < 0:
                return False
            position += 1
        return True

    def _vector_candidates(self, query_vector: list[float], grade_id: str | None, limit: int) -> dict[str, float]:
        vector_text = "[" + ",".join(map(str, query_vector)) + "]"
        where = ""
        params: list[Any] = [vector_text]
        if grade_id:
            where = "WHERE d.grade_id = %s"
            params.append(grade_id)
        params.append(vector_text)
        params.append(limit)
        sql = f"""
          SELECT e.content_id, 1 - (e.embedding <=> %s::vector) AS score
          FROM knowledge_content_embeddings e
          JOIN knowledge_contents c USING(content_id)
          JOIN knowledge_documents d USING(document_id)
          {('WHERE d.status = \'published\' AND ' + where[6:]) if where else 'WHERE d.status = \'published\''}
          ORDER BY e.embedding <=> %s::vector
          LIMIT %s
        """
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return {content_id: float(score) for content_id, score in cur.fetchall()}

    def search(self, query: str, top_k: int = 5, *, student_id: str | None = None,
               course_id: str | None = None, grade_id: str | None = None) -> list[RetrievalHit]:
        del student_id, course_id  # Knowledge documents are currently public by design.
        query = query.strip()
        if not query:
            return []
        query_tokens = tokenize(query)
        route = self._route(query, query_tokens)
        query_vector = self.embedding.embed(query)
        vector_scores = self._vector_candidates(query_vector, grade_id,
                                                max(top_k, top_k * int(route["vector_pool"])))
        lexical_scores_all = [float(value) for value in self._bm25.get_scores(query_tokens)] if self._bm25 else [0.0] * len(self.chunks)
        lexical_ids = [chunk.chunk_id for index, chunk in enumerate(self.chunks)
                       if lexical_scores_all[index] > 0 and (not grade_id or chunk.metadata.get("grade_id") == grade_id)]
        lexical_ids.sort(key=lambda content_id: (-lexical_scores_all[self._chunk_index[content_id]], content_id))
        lexical_ids = lexical_ids[:max(top_k, top_k * int(route["bm25_pool"]))]
        candidate_ids = set(vector_scores) | set(lexical_ids)
        vector_rank = {content_id: index + 1 for index, content_id in
                       enumerate(sorted(vector_scores, key=vector_scores.get, reverse=True))}
        lexical_rank = {content_id: index + 1 for index, content_id in enumerate(lexical_ids)}
        hits: list[RetrievalHit] = []
        for content_id in candidate_ids:
            index = self._chunk_index[content_id]
            chunk = self.chunks[index]
            if grade_id and chunk.metadata.get("grade_id") != grade_id:
                continue
            lexical_score = lexical_scores_all[index]
            hit = RetrievalHit(chunk, float(route["vector_weight"]) / (60 + vector_rank.get(content_id, 9999)) +
                               (float(route["bm25_weight"]) / (60 + lexical_rank[content_id]) if content_id in lexical_rank else 0.0),
                               vector_scores.get(content_id, 0.0), lexical_score)
            searchable = " ".join((chunk.text, chunk.title, chunk.chapter_id, *chunk.knowledge_point_ids))
            hit.chunk.metadata.update({"meaningful_overlap": meaningful_overlap(query, searchable),
                                       "retrieval_route": route["mode"],
                                       "bm25_weight": route["bm25_weight"],
                                       "vector_weight": route["vector_weight"]})
            hits.append(hit)
        hits = self.reranker.rerank(query, hits)
        for rank, hit in enumerate(hits[:top_k], 1):
            hit.rank = rank
        return hits[:top_k]
