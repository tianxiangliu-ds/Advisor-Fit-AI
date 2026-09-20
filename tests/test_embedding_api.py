"""向量接口的**真实 HTTP 链路**测试。

前面的 `test_embedding.py` 只验证了后端的选择与降级；这里起一个本地 mock 服务，
用真的 httpx 客户端打过去——证明 `ApiEmbedder` 真的能收发请求，
而不是"构造得出来、跑不起来"。

不联网、不花钱：mock 服务只认自己的响应格式。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from advisor_fit.providers.embedding import ApiEmbedder, build_embedder

# 记录 mock 服务收到了什么，供断言检查
RECEIVED: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定名
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        body = json.loads(raw)
        RECEIVED.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            }
        )

        inputs = body.get("input") or []
        # 造一个"同义句相似"的假向量：含"知识图谱"的给第一维，含"图谱"的也给第一维
        data = []
        for index, text in enumerate(inputs):
            text = str(text)
            vector = [0.0, 0.0, 0.0]
            if "知识图谱" in text or "图谱" in text:
                vector[0] = 1.0
            elif "油画" in text:
                vector[2] = 1.0
            else:
                vector[1] = 1.0
            data.append({"index": index, "embedding": vector, "object": "embedding"})

        payload = json.dumps({"data": data, "model": body.get("model", ""), "object": "list"})
        raw_bytes = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw_bytes)))
        self.end_headers()
        self.wfile.write(raw_bytes)

    def log_message(self, *args) -> None:
        """别往测试输出里刷访问日志。"""


@pytest.fixture
def mock_endpoint():
    RECEIVED.clear()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_api_embedder_returns_normalised_vectors(mock_endpoint):
    with httpx.Client() as client:
        embedder = ApiEmbedder(
            client=client, model="fake-embedding", base_url=mock_endpoint, api_key="sk-test"
        )
        vectors = embedder.embed(["知识图谱构建", "油画创作"])

    assert len(vectors) == 2
    assert all(abs(sum(v * v for v in row) ** 0.5 - 1.0) < 1e-6 for row in vectors)
    assert embedder.dim == 3


def test_api_embedder_sends_key_and_payload(mock_endpoint):
    with httpx.Client() as client:
        embedder = ApiEmbedder(
            client=client, model="fake-embedding", base_url=mock_endpoint, api_key="sk-test"
        )
        embedder.embed(["知识图谱"])

    assert RECEIVED, "mock 服务没收到请求"
    sent = RECEIVED[0]
    assert sent["path"] == "/v1/embeddings"
    assert sent["auth"] == "Bearer sk-test"
    assert sent["body"]["model"] == "fake-embedding"
    assert sent["body"]["input"] == ["知识图谱"]


def test_api_embedder_omits_the_header_when_no_key(mock_endpoint):
    """有些自建服务不需要 Key，这时不该硬塞一个空的 Authorization。"""
    with httpx.Client() as client:
        embedder = ApiEmbedder(client=client, model="m", base_url=mock_endpoint, api_key="")
        embedder.embed(["知识图谱"])

    assert RECEIVED[0]["auth"] is None


def test_semantic_style_similarity_works_end_to_end(mock_endpoint):
    """真语义后端下，"知识图谱"与"图谱构建"应当判为相近，与"油画"不相近。"""
    from advisor_fit.providers.embedding import cosine

    with httpx.Client() as client:
        embedder = ApiEmbedder(client=client, model="m", base_url=mock_endpoint)
        query, related, unrelated = embedder.embed(["知识图谱", "图谱构建", "油画"])

    assert cosine(query, related) > cosine(query, unrelated)
    assert cosine(query, unrelated) == pytest.approx(0.0)


def test_api_backend_is_semantic_and_uses_higher_thresholds(mock_endpoint):
    with httpx.Client() as client:
        embedder = ApiEmbedder(client=client, model="m", base_url=mock_endpoint)

    assert embedder.semantic is True
    assert embedder.name == "api:m"
    # 真模型的分值尺度更高，门槛必须跟着抬
    assert embedder.reason_threshold == 0.45
    assert embedder.recall_threshold == 0.60


def test_build_embedder_picks_the_api_backend_from_settings(mock_endpoint):
    """配了地址与模型就能用——这正是"配一个接口就能接上"的含义。"""
    class Configured:
        embedding_base_url = mock_endpoint
        embedding_api_key = "sk-test"
        embedding_model = "fake-embedding"
        embedding_local_model = ""

    embedder = build_embedder(settings_obj=Configured())

    assert isinstance(embedder, ApiEmbedder)
    vectors = embedder.embed(["知识图谱"])
    assert len(vectors) == 1
    assert RECEIVED and RECEIVED[0]["auth"] == "Bearer sk-test"


def test_direction_search_uses_the_semantic_wording_with_the_api_backend(mock_endpoint):
    """接上真模型后，界面上的措辞要从「字面相近」变成「语义相近」。"""
    from advisor_fit.analysis.direction_search import rank_candidates
    from advisor_fit.models.advisor import Advisor

    with httpx.Client() as client:
        embedder = ApiEmbedder(client=client, model="m", base_url=mock_endpoint)
        hits = rank_candidates(
            [Advisor(university="U", department="D", name="甲",
                     research_directions=["知识图谱"], title="教授")],
            ["知识图谱"],
            limit=3,
            embedder=embedder,
        )

    assert hits and "语义相近" in hits[0].reason_text
    assert "字面相近" not in hits[0].reason_text
