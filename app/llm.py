"""LLM client with native tool-calling.

The agent reasons through chat-completions with tools. Two transports are
supported behind one interface:

- ``ollama``  — Ollama's native ``/api/chat`` (supports tools and keep_alive,
  so the model stays warm between agent steps).
- ``openai``  — any OpenAI-compatible ``/v1/chat/completions`` endpoint.

Both return an :class:`AssistantTurn` containing the assistant text, any
structured tool calls, and the wall-clock latency so every reasoning step can
be measured and logged.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx


logger = logging.getLogger("llm")


class LLMError(Exception):
    """The reasoning model could not produce a usable reply."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_message(self) -> dict[str, Any]:
        """Render back into a chat message for the conversation history."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class LLMClient(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> AssistantTurn:
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        ...


class OllamaClient(LLMClient):
    """Native Ollama chat with tool support (``POST /api/chat``)."""

    def __init__(self, model: str, base_url: str, timeout: int = 120) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/").removesuffix("/v1")
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> AssistantTurn:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._encode_messages(messages),
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": temperature},
        }
        if tools:
            body["tools"] = tools
        # qwen3-style thinking models: disable thinking for latency
        if self._model.startswith(("qwen3", "deepseek-r1")):
            body["think"] = False

        start = time.perf_counter()
        try:
            response = self._client.post(f"{self._base_url}/api/chat", json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        message = data.get("message") or {}
        tool_calls = [
            ToolCall(
                id=raw.get("id") or f"call_{index}",
                name=(raw.get("function") or {}).get("name", ""),
                arguments=_ensure_dict((raw.get("function") or {}).get("arguments")),
            )
            for index, raw in enumerate(message.get("tool_calls") or [])
        ]
        turn = AssistantTurn(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            latency_ms=latency_ms,
        )
        logger.info(
            "LLM turn completed",
            extra={
                "provider": "ollama",
                "model": self._model,
                "latency_ms": latency_ms,
                "tool_calls": [c.name for c in tool_calls],
                "content_chars": len(turn.content),
            },
        )
        return turn

    @staticmethod
    def _encode_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ollama expects tool_calls in its own shape and plain-text tool replies."""
        encoded: list[dict[str, Any]] = []
        for message in messages:
            item = dict(message)
            if item.get("role") == "assistant" and item.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": _ensure_dict(call["function"]["arguments"]),
                        }
                    }
                    for call in item["tool_calls"]
                ]
            encoded.append(item)
        return encoded


class OpenAICompatibleClient(LLMClient):
    """Any OpenAI-compatible ``/v1/chat/completions`` endpoint."""

    def __init__(self, model: str, base_url: str, api_key: str | None, timeout: int = 120) -> None:
        self._model = model
        base = base_url.rstrip("/")
        self._url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
        self._api_key = api_key
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> AssistantTurn:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": self._encode_messages(messages),
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        start = time.perf_counter()
        try:
            response = self._client.post(self._url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        message = (data.get("choices") or [{}])[0].get("message") or {}
        tool_calls = [
            ToolCall(
                id=raw.get("id") or f"call_{index}",
                name=(raw.get("function") or {}).get("name", ""),
                arguments=_parse_arguments((raw.get("function") or {}).get("arguments")),
            )
            for index, raw in enumerate(message.get("tool_calls") or [])
        ]
        turn = AssistantTurn(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            latency_ms=latency_ms,
        )
        logger.info(
            "LLM turn completed",
            extra={
                "provider": "openai",
                "model": self._model,
                "latency_ms": latency_ms,
                "tool_calls": [c.name for c in tool_calls],
                "content_chars": len(turn.content),
            },
        )
        return turn

    @staticmethod
    def _encode_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI expects tool-call arguments as JSON strings."""
        encoded: list[dict[str, Any]] = []
        for message in messages:
            item = dict(message)
            if item.get("role") == "assistant" and item.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": json.dumps(_ensure_dict(call["function"]["arguments"])),
                        },
                    }
                    for call in item["tool_calls"]
                ]
            encoded.append(item)
        return encoded


def _ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return _parse_arguments(value)


def _parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Could not parse tool-call arguments", extra={"raw": value[:500]})
    return {}


_CLIENT_INSTANCE: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _CLIENT_INSTANCE
    if _CLIENT_INSTANCE is None:
        from app.config import get_settings

        settings = get_settings()
        provider = (settings.llm_provider or "ollama").strip().lower()
        if provider == "openai":
            if not settings.llm_api_key:
                logger.warning("LLM_PROVIDER=openai but LLM_API_KEY is empty")
            _CLIENT_INSTANCE = OpenAICompatibleClient(
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                timeout=settings.llm_timeout,
            )
        else:
            _CLIENT_INSTANCE = OllamaClient(
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout,
            )
        logger.info(
            "LLM client initialized",
            extra={"provider": provider, "model": settings.llm_model},
        )
    return _CLIENT_INSTANCE


def reset_llm_client() -> None:
    global _CLIENT_INSTANCE
    _CLIENT_INSTANCE = None
