from __future__ import annotations

import hashlib
import math
import re


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
STOPWORDS = set("的了是和与或什么怎么如何请问一下这个那个可以能够我你他它我们他们有关关于吗呢？?！!，,。." )


def tokenize(text: str) -> list[str]:
    return [item.lower() for item in TOKEN_RE.findall(text)]


def meaningful_tokens(text: str) -> list[str]:
    return [token for token in tokenize(text) if token not in STOPWORDS]


def meaningful_overlap(query: str, text: str) -> int:
    """Match Chinese concepts even when a question suffix follows the concept."""
    query_runs = re.findall(r"[\u4e00-\u9fff]+", query)
    phrase_hits = 0
    for run in query_runs:
        # Character n-grams handle queries such as “一元一次方程怎么解” where the
        # knowledge-point phrase is embedded inside a longer natural-language run.
        ngrams = {run[i:i + size] for size in range(2, min(7, len(run) + 1))
                  for i in range(len(run) - size + 1)}
        phrase_hits += sum(1 for phrase in ngrams if phrase not in STOPWORDS and phrase in text)
    ascii_hits = len(set(re.findall(r"[A-Za-z0-9_]+", query.lower())) &
                     set(re.findall(r"[A-Za-z0-9_]+", text.lower())))
    return phrase_hits + ascii_hits


class HashEmbedding:
    """无需下载模型即可复现的向量基线，生产环境替换为真实 Embedding。"""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = tokenize(text)
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
