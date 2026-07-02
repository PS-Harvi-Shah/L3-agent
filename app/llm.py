import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        ...

    @abstractmethod
    def chat_structured(
        self, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> dict[str, Any]:
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        ...


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 30,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    @property
    def is_available(self) -> bool:
        return bool(self._api_key) and self._api_key != "sk-placeholder"

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def chat_structured(
        self, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> dict[str, Any]:
        content = self.chat(messages, temperature)
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            cleaned = cleaned.rsplit("```", 1)[0] if "```" in cleaned else cleaned
            cleaned = cleaned.strip()
        return json.loads(cleaned)


class OllamaProvider(LLMProvider):
    """OpenAI-compatible client for a local Ollama server."""

    def __init__(
        self,
        model: str = "qwen3:8b",
        base_url: str = "http://localhost:11434/v1",
        timeout: int = 60,
    ) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    @property
    def is_available(self) -> bool:
        return True

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Content-Type": "application/json"},
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def chat_structured(
        self, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> dict[str, Any]:
        content = self.chat(messages, temperature)
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            cleaned = cleaned.rsplit("```", 1)[0] if "```" in cleaned else cleaned
            cleaned = cleaned.strip()
        return json.loads(cleaned)


class NoopProvider(LLMProvider):
    @property
    def is_available(self) -> bool:
        return False

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        return ""

    def chat_structured(
        self, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> dict[str, Any]:
        return {}


_PROVIDER_INSTANCE: LLMProvider | None = None


def get_llm_provider() -> LLMProvider:
    global _PROVIDER_INSTANCE
    if _PROVIDER_INSTANCE is not None:
        return _PROVIDER_INSTANCE

    from app.config import get_settings

    settings = get_settings()
    _PROVIDER_INSTANCE = _create_provider(settings)
    return _PROVIDER_INSTANCE


def _create_provider(settings: Any) -> LLMProvider:
    provider_type = (settings.llm_provider or "").lower().strip()

    if provider_type == "openai":
        api_key = settings.llm_api_key
        if not api_key or api_key == "sk-placeholder":
            logging.getLogger("llm").warning("OpenAI API key not configured, using NoopProvider")
            return NoopProvider()
        return OpenAIProvider(
            api_key=api_key,
            model=settings.llm_model or "gpt-4o-mini",
            base_url=settings.llm_base_url or "https://api.openai.com/v1",
            timeout=settings.llm_timeout or 30,
        )

    if provider_type == "ollama":
        return OllamaProvider(
            model=settings.llm_model or "qwen3:8b",
            base_url=settings.llm_base_url or "http://localhost:11434/v1",
            timeout=settings.llm_timeout or 60,
        )

    logging.getLogger("llm").info("No LLM provider configured, using NoopProvider")
    return NoopProvider()


def reset_llm_provider() -> None:
    global _PROVIDER_INSTANCE
    _PROVIDER_INSTANCE = None
