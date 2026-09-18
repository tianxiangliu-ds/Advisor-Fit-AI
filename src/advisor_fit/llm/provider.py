"""多 provider 结构化 LLM：统一 generate(schema, ...) 接口，多后端实现。

照 Pi 的 pi-ai 思想：上层只关心「给 schema + 指令 + payload，拿回 Pydantic 模型」，
不同 provider（DeepSeek / OpenAI / Anthropic / Ollama）的 HTTP 差异在内部抹平。
"""

from __future__ import annotations

import json
from typing import Protocol

import httpx
from pydantic import BaseModel

from advisor_fit.config import settings


class LLMUnavailable(Exception):
    """LLM 不可用（未配置 Key / 网络失败 / 解析失败）。"""


class StructuredLLM(Protocol):
    def generate(
        self, *, schema: type[BaseModel], instructions: str, payload: dict
    ) -> BaseModel: ...


def _build_prompt(
    schema: type[BaseModel], instructions: str, payload: dict
) -> tuple[str, str]:
    schema_json = schema.model_json_schema()
    system = (
        "你只返回符合给定 JSON Schema 的合法 JSON 对象。"
        "不得编造事实，只能使用 payload 中提供的证据与事实。"
        "只输出 JSON 对象本身，不要用 Markdown 代码块包裹。"
    )
    user = (
        f"{instructions}\n\n"
        f"Payload:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"JSON Schema:\n{json.dumps(schema_json, ensure_ascii=False)}"
    )
    return system, user


def _parse_json(schema: type[BaseModel], content: str) -> BaseModel:
    return schema.model_validate(json.loads(content))


class NullLLM:
    """未配置 Key 时的降级实现；generate 抛错，由上层走确定性模板。"""

    def generate(self, *, schema, instructions, payload):
        raise LLMUnavailable("LLM 未配置")


class OpenAICompatibleLLM:
    """DeepSeek / OpenAI 等 OpenAI 兼容 /chat/completions 后端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        store: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.store = store
        self.client = client or httpx.Client(timeout=60.0)

    def generate(self, *, schema, instructions, payload) -> BaseModel:
        if not self.api_key:
            raise LLMUnavailable("LLM_API_KEY 未配置")
        system, user = _build_prompt(schema, instructions, payload)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "store": self.store,
        }
        try:
            resp = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"LLM 调用失败: {exc}") from exc
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return _parse_json(schema, content)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"LLM 输出无法解析: {exc}") from exc


class AnthropicLLM:
    """Anthropic 兼容 /messages 后端。"""

    def __init__(
        self, api_key: str, base_url: str, model: str, client: httpx.Client | None = None
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client or httpx.Client(timeout=60.0)

    def generate(self, *, schema, instructions, payload) -> BaseModel:
        if not self.api_key:
            raise LLMUnavailable("LLM_API_KEY 未配置")
        system, user = _build_prompt(schema, instructions, payload)
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": user}],
            "system": system,
            "max_tokens": 2048,
        }
        try:
            resp = self.client.post(
                f"{self.base_url}/messages",
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                json=body,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"LLM 调用失败: {exc}") from exc
        content = "".join(
            item.get("text", "")
            for item in resp.json().get("content", [])
            if item.get("type") == "text"
        )
        try:
            return _parse_json(schema, content)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"LLM 输出无法解析: {exc}") from exc


class OllamaLLM:
    """本地 Ollama /api/chat 后端（无需 Key）。"""

    def __init__(self, base_url: str, model: str, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client or httpx.Client(timeout=120.0)

    def generate(self, *, schema, instructions, payload) -> BaseModel:
        system, user = _build_prompt(schema, instructions, payload)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
        }
        try:
            resp = self.client.post(f"{self.base_url}/api/chat", json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama 调用失败: {exc}") from exc
        content = resp.json()["message"]["content"]
        try:
            return _parse_json(schema, content)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"LLM 输出无法解析: {exc}") from exc


_PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
}


def build_llm(
    provider: str | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
) -> StructuredLLM:
    provider = (provider or settings.llm_provider).lower()
    api_key = api_key if api_key is not None else settings.llm_api_key

    if provider in _PROVIDER_DEFAULTS:
        default_base, default_model = _PROVIDER_DEFAULTS[provider]
        if not api_key:
            return NullLLM()
        return OpenAICompatibleLLM(
            api_key=api_key,
            base_url=base_url or settings.llm_base_url or default_base,
            model=model or settings.llm_model or default_model,
            store=settings.llm_store,
            client=client,
        )
    if provider == "anthropic":
        if not api_key:
            return NullLLM()
        return AnthropicLLM(
            api_key=api_key,
            base_url=base_url or settings.llm_base_url or "https://api.anthropic.com/v1",
            model=model or settings.llm_model or "claude-sonnet-5",
            client=client,
        )
    if provider == "ollama":
        return OllamaLLM(
            base_url=base_url or settings.llm_base_url or "http://localhost:11434",
            model=model or settings.llm_model or "qwen2.5:7b",
            client=client,
        )
    raise ValueError(f"未知 LLM provider: {provider}")
