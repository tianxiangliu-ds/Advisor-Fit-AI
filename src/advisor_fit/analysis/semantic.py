"""语义召回：给关键词匹配补一层"意思相近"的召回。

**它在整条链路里的位置**：关键词匹配是主力，负责精确、可解释、零成本；
语义召回是补充，负责捞回"没命中字面但意思接近"的候选。两者在结果里
**分开标注**，不混成一个分数——项目的红线是每条推荐理由都要说得清，
语义相似度也必须以它自己的名义出现，不能伪装成关键词命中。

**能力边界要说清楚**：
- 配了 embedding 接口（或装了本机模型）时，是真正的近义/同义召回；
- 默认的离线档只做**表层相似**（词序、部分重合、写法差异），
  同义词它认不出来。`Embedder.semantic` 会如实反映这一点，界面据此措辞。
"""

from __future__ import annotations

from collections.abc import Sequence

from advisor_fit.models.advisor import Advisor
from advisor_fit.providers.embedding import Embedder, cosine


def advisor_document(advisor: Advisor) -> str:
    """把一位导师压成一段用于比对的文本。

    只用**能代表研究方向**的字段：研究方向、研究领域、院系、代表论文标题。
    刻意不把 `profile_text` 整段放进来——官网简介里常有大段套话，
    会让所有导师看起来都差不多。
    """
    parts: list[str] = []
    parts.extend(str(item) for item in (advisor.research_directions or []))
    parts.extend(str(item) for item in (advisor.research_areas or []))
    if advisor.department:
        parts.append(str(advisor.department))
    parts.extend(str(item) for item in (advisor.publications or [])[:8])
    return "；".join(part for part in parts if part)


def semantic_scores(
    query: str, advisors: Sequence[Advisor], embedder: Embedder
) -> list[float]:
    """算出每位导师与查询的相似度（0–1）。

    任何一步失败都返回全 0——语义是**补充能力**，不该让主流程失败。
    """
    if not query.strip() or not advisors:
        return [0.0] * len(advisors)

    documents = [advisor_document(advisor) for advisor in advisors]
    try:
        vectors = embedder.embed([query, *documents])
    except Exception:  # noqa: BLE001 - 向量服务不可用就当作没有语义信号
        return [0.0] * len(advisors)
    if len(vectors) != len(documents) + 1:
        return [0.0] * len(advisors)

    query_vector, document_vectors = vectors[0], vectors[1:]
    return [cosine(query_vector, document) for document in document_vectors]
