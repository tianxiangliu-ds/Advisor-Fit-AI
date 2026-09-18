"""结构化 LLM 生成协议与 OpenAI 兼容实现。

StructuredLLM.generate() 只返回 Pydantic schema；payload 中只传短证据文本、证据 ID
与已脱敏的学生事实。v0.1 只保证 OpenAI 兼容接口这一套配置，其它服务由部署者自测。
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


class OpenAIStructuredLLM:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        store: bool | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model or "gpt-4o-mini"
        self.store = store if store is not None else settings.llm_store

    def generate(
        self, *, schema: type[BaseModel], instructions: str, payload: dict
    ) -> BaseModel:
        if not self.api_key:
            raise LLMUnavailable("LLM_API_KEY 未配置")

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
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=60.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"LLM 调用失败: {exc}") from exc

        content = resp.json()["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
            return schema.model_validate(data)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMUnavailable(f"LLM 输出无法解析: {exc}") from exc
