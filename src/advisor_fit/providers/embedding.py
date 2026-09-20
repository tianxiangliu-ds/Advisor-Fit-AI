"""文本向量化：为"语义召回"提供可替换的后端。

**为什么默认档不做真正的语义**：本项目的硬约束是「装完就能跑、默认主干免费免 Key」，
而真正的语义向量要么得下载模型（上百 MB），要么得调 API（要 Key）。
所以这里分三档，能力递进、逐档降级：

| 档位 | 何时启用 | 能力 | 代价 |
|---|---|---|---|
| `hashing-ngram` | 默认 | **表层相似**：词序不同、部分重合、写法差异 | 零依赖、离线、快 |
| `local-model` | 装了 `embed` 可选分组 | 真正的同义/近义理解 | 首次要下模型 |
| `api` | 配了 embedding 接口 | 真正的同义/近义理解 | 要 Key、按量计费 |

**必须说清楚**：默认档是**词形层面的**相似度，不是同义词理解。把它说成"语义召回"
是夸大。它的真实价值是"不配任何东西也能用一个比关键词更宽松的召回"，
真正的语义能力要配了 API 才有。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

# 中英混合文本切分：连续汉字按 2-gram 处理，拉丁词按单词
_CJK_RE = re.compile(r"[\u4e00-\u9fa5]+")
_LATIN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,}")

# 中文 2-gram 之外再补一条整词特征，避免"知识图谱"与"图谱知识"被当成完全一样
_NGRAM = 2


class Embedder(Protocol):
    """向量后端。实现可以是本地的，也可以是远程的。"""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    @property
    def semantic(self) -> bool:
        """是不是**真正的语义**向量。默认档为 False，界面据此措辞。"""
        ...

    @property
    def reason_threshold(self) -> float:
        """相似度超过它才值得提一句「语义相近」。"""
        ...

    @property
    def recall_threshold(self) -> float:
        """关键词一个都没中时，超过它才单独靠语义召回。"""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _features(text: str) -> list[str]:
    """把一段文本拆成用于相似度计算的表层特征。"""
    text = (text or "").lower()
    features: list[str] = []
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            features.append(run)
            continue
        features.append(run)  # 整段作为一个特征
        features.extend(run[i : i + _NGRAM] for i in range(len(run) - _NGRAM + 1))
    features.extend(_LATIN_RE.findall(text))
    return features


def _bucket(feature: str, dim: int) -> int:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


@dataclass
class HashingEmbedder:
    """默认档：把表层特征哈希进固定维度的桶，再归一化。

    零依赖、离线、确定性（同输入永远同输出）。它捕捉的是**词形层面的重合**：
    词序不同、部分重合、写法差异。**不捕捉同义词**——"图神经网络"和"GNN"
    在它眼里没有关系。
    """

    dim: int = 512

    @property
    def name(self) -> str:
        return "hashing-ngram"

    @property
    def semantic(self) -> bool:
        return False

    # 下面两个数**是按真实导师库校准出来的**，不是拍脑袋的：
    # 拿 1200 位真实导师、三个查询实测，最高相似度只有 0.33~0.41，
    # 前 1% 分位约 0.20~0.24，一半以上的导师完全无字面重合。
    # 所以离线档的分值尺度天然比真模型低得多——门槛必须跟着后端走，
    # 用一套通用阈值会让这档的"仅语义召回"永远触发不了。
    @property
    def reason_threshold(self) -> float:
        return 0.20

    @property
    def recall_threshold(self) -> float:
        return 0.36

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dim
            for feature in _features(text):
                vector[_bucket(feature, self.dim)] += 1.0
            vectors.append(_normalise(vector))
        return vectors


@dataclass
class ApiEmbedder:
    """调 OpenAI 兼容的 `/embeddings` 接口。

    大多数国内厂商（硅基流动、智谱、阿里百炼、百度千帆等）都提供 OpenAI 兼容的
    embedding 接口，填 `base_url` + `model` + `api_key` 即可。
    注意 **DeepSeek 目前不提供 embedding 接口**，拿 DeepSeek 的 Key 配这里不会生效。
    """

    client: Any
    model: str
    base_url: str
    api_key: str = ""
    timeout: float = 30.0
    _dim: int = 0

    @property
    def name(self) -> str:
        return f"api:{self.model}"

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def semantic(self) -> bool:
        return True

    # 真模型的分值尺度明显更高（同义句普遍在 0.6 以上），门槛相应抬高
    @property
    def reason_threshold(self) -> float:
        return 0.45

    @property
    def recall_threshold(self) -> float:
        return 0.60

    def _headers(self) -> dict[str, str]:
        # Key 按请求带，**不去改共享 client 的 headers**——那会污染别的请求
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self.client.post(
            f"{self.base_url.rstrip('/')}/embeddings",
            json={"model": self.model, "input": list(texts)},
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        vectors = [_normalise([float(v) for v in row["embedding"]]) for row in rows]
        if vectors:
            self._dim = len(vectors[0])
        return vectors


@dataclass
class LocalModelEmbedder:
    """可选档：本机跑 sentence-transformers 模型（需自己装 `embed` 分组）。"""

    model: Any
    model_name: str

    @property
    def name(self) -> str:
        return f"local:{self.model_name}"

    @property
    def dim(self) -> int:
        return int(self.model.get_sentence_embedding_dimension() or 0)

    @property
    def semantic(self) -> bool:
        return True

    @property
    def reason_threshold(self) -> float:
        return 0.45

    @property
    def recall_threshold(self) -> float:
        return 0.60

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raw = self.model.encode(list(texts), normalize_embeddings=True)
        return [[float(value) for value in row] for row in raw]


def build_embedder(*, client: Any = None, settings_obj: Any = None) -> Embedder:
    """按配置挑一个后端。**任何一步失败都退回默认档，绝不抛异常。**

    顺序：显式配置的 API > 装了本地模型 > 默认的离线档。
    """
    settings_obj = settings_obj or _settings()

    base_url = str(getattr(settings_obj, "embedding_base_url", "") or "").strip()
    model = str(getattr(settings_obj, "embedding_model", "") or "").strip()
    api_key = str(getattr(settings_obj, "embedding_api_key", "") or "").strip()
    if base_url and model:
        if client is None:
            try:
                import httpx  # noqa: PLC0415

                client = httpx.Client(timeout=30.0)
            except Exception:  # noqa: BLE001 - 连 httpx 都没有就退回离线档
                return HashingEmbedder()
        return ApiEmbedder(
            client=client, model=model, base_url=base_url, api_key=api_key
        )

    local_name = str(getattr(settings_obj, "embedding_local_model", "") or "").strip()
    if local_name:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            return LocalModelEmbedder(
                model=SentenceTransformer(local_name), model_name=local_name
            )
        except Exception:  # noqa: BLE001 - 没装或下不动就退回默认档
            pass

    return HashingEmbedder()


def _settings() -> Any:
    from advisor_fit.config import settings  # noqa: PLC0415

    return settings


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """两个已归一化向量的余弦相似度；维度不一致时按 0 处理。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right, strict=True)))
