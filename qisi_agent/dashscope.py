"""阿里百炼 OpenAI 兼容 API 适配层。

Key 只从环境变量读取，不在代码、索引或日志中持久化。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional cloud dependency
    load_dotenv = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional cloud dependency
    OpenAI = None


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_CHAT_MODEL = "qwen-plus"


class DashScopeError(RuntimeError):
    """百炼配置、请求或响应校验失败。"""


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise DashScopeError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise DashScopeError(f"{name} 必须大于 0")
    return value


@dataclass(frozen=True)
class DashScopeConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 10
    chat_model: str = DEFAULT_CHAT_MODEL
    timeout_seconds: float = 60.0
    max_retries: int = 2
    temperature: float = 0.0
    max_tokens: int = 1200

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise DashScopeError("DASHSCOPE_API_KEY 未配置")
        if not self.base_url.strip() or not self.embedding_model.strip() or not self.chat_model.strip():
            raise DashScopeError("百炼 base_url、Embedding 模型和对话模型不能为空")
        if self.embedding_dimensions <= 0 or not 1 <= self.embedding_batch_size <= 10:
            raise DashScopeError("Embedding 维度必须大于 0，批量大小必须在 1-10 之间")
        if self.timeout_seconds <= 0 or self.max_retries < 0:
            raise DashScopeError("超时必须大于 0，重试次数不能小于 0")
        if not 0 <= self.temperature <= 2 or self.max_tokens <= 0:
            raise DashScopeError("temperature 或 max_tokens 配置无效")

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> "DashScopeConfig":
        if env_file and load_dotenv:
            load_dotenv(dotenv_path=env_file, override=False)
        return cls(
            api_key=os.getenv("DASHSCOPE_API_KEY", ""),
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            embedding_dimensions=_int_env("EMBEDDING_DIMENSIONS", 1024),
            embedding_batch_size=_int_env("EMBEDDING_BATCH_SIZE", 10),
            chat_model=os.getenv("CHAT_MODEL", DEFAULT_CHAT_MODEL),
            timeout_seconds=float(os.getenv("CHAT_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.getenv("CHAT_MAX_RETRIES", "2")),
            temperature=float(os.getenv("CHAT_TEMPERATURE", "0")),
            max_tokens=_int_env("CHAT_MAX_TOKENS", 1200),
        )


class DashScopeEmbeddingService:
    """批量调用 text-embedding-v4，并验证维度、顺序和数值。"""

    def __init__(self, config: DashScopeConfig, *, client: Any | None = None):
        self.config = config
        self.model = config.embedding_model
        self.dimensions = config.embedding_dimensions
        if client is not None:
            self.client = client
        elif OpenAI is None:
            raise DashScopeError("云端模式需要安装 openai 和 python-dotenv：pip install -e '.[cloud]'")
        else:
            self.client = OpenAI(api_key=config.api_key, base_url=config.base_url,
                                 timeout=config.timeout_seconds, max_retries=config.max_retries)

    def _validate(self, vector: Iterable[float], label: str) -> list[float]:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError, OverflowError) as exc:
            raise DashScopeError(f"{label} 的 Embedding 含有非数值项") from exc
        if len(values) != self.config.embedding_dimensions:
            raise DashScopeError(f"{label} 维度错误：期望 {self.config.embedding_dimensions}，实际 {len(values)}")
        if not all(math.isfinite(value) for value in values) or not any(values):
            raise DashScopeError(f"{label} 的 Embedding 不是有效非零向量")
        return values

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        values = list(texts)
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise DashScopeError("Embedding 输入必须是非空文本")
        result: list[list[float]] = []
        for start in range(0, len(values), self.config.embedding_batch_size):
            batch = values[start:start + self.config.embedding_batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.config.embedding_model,
                    input=batch,
                    dimensions=self.config.embedding_dimensions,
                    encoding_format="float",
                )
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                suffix = f" HTTP {status}" if status is not None else ""
                raise DashScopeError(f"Embedding API 请求失败：{type(exc).__name__}{suffix}") from exc
            items = list(getattr(response, "data", ()))
            if len(items) != len(batch):
                raise DashScopeError(f"Embedding API 返回数量错误：期望 {len(batch)}，实际 {len(items)}")
            indexed: dict[int, list[float]] = {}
            for item in items:
                index = getattr(item, "index", None)
                if not isinstance(index, int) or not 0 <= index < len(batch) or index in indexed:
                    raise DashScopeError("Embedding API 返回了无效或重复 index")
                indexed[index] = self._validate(getattr(item, "embedding", None), f"text-{start + index + 1}")
            if set(indexed) != set(range(len(batch))):
                raise DashScopeError("Embedding API 返回结果缺少输入项")
            result.extend(indexed[index] for index in range(len(batch)))
        return result

    def embed(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


@dataclass(frozen=True)
class DashScopeChatResponse:
    content: str
    model: str


class DashScopeChatService:
    def __init__(self, config: DashScopeConfig, *, client: Any | None = None):
        self.config = config
        if client is not None:
            self.client = client
        elif OpenAI is None:
            raise DashScopeError("云端模式需要安装 openai：pip install -e '.[cloud]'")
        else:
            self.client = OpenAI(api_key=config.api_key, base_url=config.base_url,
                                 timeout=config.timeout_seconds, max_retries=config.max_retries)

    def complete(self, messages: Iterable[dict[str, str]]) -> DashScopeChatResponse:
        message_list = list(messages)
        if not message_list or any(not isinstance(item.get("content"), str) for item in message_list):
            raise DashScopeError("对话 messages 必须是非空文本对象")
        try:
            response = self.client.chat.completions.create(
                model=self.config.chat_model,
                messages=message_list,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            content = response.choices[0].message.content
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            suffix = f" HTTP {status}" if status is not None else ""
            raise DashScopeError(f"对话 API 请求失败：{type(exc).__name__}{suffix}") from exc
        if not isinstance(content, str) or not content.strip():
            raise DashScopeError("对话 API 返回空内容")
        return DashScopeChatResponse(content.strip(), str(getattr(response, "model", self.config.chat_model)))

    def stream(self, messages: Iterable[dict[str, str]]):
        """Yield response text deltas from the provider's streaming API."""
        message_list = list(messages)
        if not message_list or any(not isinstance(item.get("content"), str) for item in message_list):
            raise DashScopeError("对话 messages 必须是非空文本对象")
        try:
            response = self.client.chat.completions.create(
                model=self.config.chat_model, messages=message_list,
                temperature=self.config.temperature, max_tokens=self.config.max_tokens,
                stream=True,
            )
            for item in response:
                delta = getattr(getattr(item, "choices", [None])[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    yield str(content)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            suffix = f" HTTP {status}" if status is not None else ""
            raise DashScopeError(f"对话流式 API 请求失败：{type(exc).__name__}{suffix}") from exc
