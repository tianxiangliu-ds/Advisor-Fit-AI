"""多 provider 结构化 LLM 的工厂与生成测试（不触真实网络）。"""

import json

import httpx
import pytest
from pydantic import BaseModel

from advisor_fit.llm.provider import (
    AnthropicLLM,
    LLMUnavailable,
    NullLLM,
    OllamaLLM,
    OpenAICompatibleLLM,
    build_llm,
)


class _Out(BaseModel):
    name: str
    score: int


def _client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_build_llm_defaults_deepseek_to_openai_compatible():
    llm = build_llm("deepseek", api_key="sk-x")
    assert isinstance(llm, OpenAICompatibleLLM)
    assert llm.base_url == "https://api.deepseek.com/v1"
    assert llm.model == "deepseek-chat"


def test_build_llm_returns_null_when_no_key():
    assert isinstance(build_llm("openai", api_key=""), NullLLM)
    assert isinstance(build_llm("anthropic", api_key=""), NullLLM)


def test_build_llm_selects_anthropic_and_ollama():
    assert isinstance(build_llm("anthropic", api_key="sk-x"), AnthropicLLM)
    assert isinstance(build_llm("ollama"), OllamaLLM)


def test_ollama_needs_no_api_key():
    assert not isinstance(build_llm("ollama"), NullLLM)


def test_openai_compatible_generate_parses_json():
    llm = OpenAICompatibleLLM(
        "sk-x",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
        client=_client(
            {"choices": [{"message": {"content": json.dumps({"name": "n", "score": 1})}}]}
        ),
    )
    assert llm.generate(schema=_Out, instructions="x", payload={}) == _Out(name="n", score=1)


def test_openai_compatible_requires_key():
    llm = OpenAICompatibleLLM("", "https://api.deepseek.com/v1", "deepseek-chat")
    with pytest.raises(LLMUnavailable):
        llm.generate(schema=_Out, instructions="x", payload={})


def test_null_llm_raises_llm_unavailable():
    with pytest.raises(LLMUnavailable):
        NullLLM().generate(schema=_Out, instructions="x", payload={})
