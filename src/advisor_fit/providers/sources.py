"""学术检索源登记表：哪个学科查哪些库、先查谁、要不要 Key、每秒几次。

**这张表是配置，不是代码。** 想调整检索顺序、临时关掉某个库、加一个新的免费库，
改 `config/sources.json` 即可，不必改 Python：

```json
{
  "sources": [
    {"key": "openalex", "rank": 5, "enabled": false}
  ],
  "discipline_keywords": {"cs": ["计算机", "人工智能"]}
}
```

规则：
- 只写要**覆盖**的字段即可，没写的沿用内置默认值；
- `key` 与内置源同名 → 覆盖该源的字段；新 `key` → 追加一个新源（必须给 label）；
- 文件不存在或写坏了 → 静默用内置默认值，绝不让配置问题阻断检索。

内置原则：**默认主干全是免费、免申请 Key 的公开学术库**，
新用户装完就能直接检索；万方这类需要申请 Key 的源只在配好 Key 后启用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from advisor_fit.providers.disciplines import DEFAULT_DISCIPLINE_KEYWORDS

CONFIG_RELATIVE_PATH = Path("config") / "sources.json"

# 通配学科：任何学科都查
ALL_DISCIPLINES = ("*",)


@dataclass(frozen=True)
class SourceSpec:
    key: str
    label: str
    disciplines: tuple[str, ...] = ALL_DISCIPLINES
    rank: int = 50  # 数字越小越先查
    language: str = "en"  # "en" 国际库 / "zh" 中文库
    requires_key: str = ""  # 需要哪个 settings 字段；空 = 免费免 Key
    cost_per_call: float = 0.0  # 人民币；免费源为 0
    min_interval_seconds: float = 0.5  # 同一来源两次请求的最小间隔（礼貌限速）
    supports_author_search: bool = True
    supports_title_search: bool = True
    enabled: bool = True
    note: str = ""

    def covers(self, discipline: str | None) -> bool:
        return "*" in self.disciplines or (discipline or "general") in self.disciplines


DEFAULT_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        key="openalex",
        label="OpenAlex · 全学科",
        rank=10,
        min_interval_seconds=0.2,
        note="全学科主干库，约 2.5 亿条文献，自带学科分类与作者机构",
    ),
    SourceSpec(
        key="dblp",
        label="DBLP · 计算机",
        disciplines=("cs",),
        rank=5,
        min_interval_seconds=1.0,
        enabled=False,
        note=(
            "计算机领域会议与期刊的权威目录（无摘要）。目前 DBLP 启用了 Anubis 反爬校验，"
            "程序请求会拿到 HTML 挑战页而不是 JSON，所以默认关闭；"
            "计算机领域的召回由 OpenAlex 承担。DBLP 取消校验后把 enabled 改回 true 即可。"
        ),
    ),
    SourceSpec(
        key="europepmc",
        label="Europe PMC · 生物医学",
        disciplines=("medicine",),
        rank=5,
        min_interval_seconds=0.5,
        note="生物医学与生命科学文献，含摘要与预印本",
    ),
    SourceSpec(
        key="arxiv",
        label="arXiv · 预印本",
        disciplines=("physics", "cs", "chemistry", "engineering", "economics"),
        rank=15,
        min_interval_seconds=3.0,
        note="物理/数学/计算机等预印本；官方要求请求间隔 ≥3 秒",
    ),
    SourceSpec(
        key="crossref",
        label="Crossref · DOI 元数据",
        rank=30,
        min_interval_seconds=0.5,
        supports_author_search=False,
        note=(
            "全学科 DOI 元数据，主要用来按标题补齐标题/年份/DOI。"
            "它的作者查询只是模糊全文检索（实测查「张伟」会返回煤矿论文），"
            "所以不参与「按作者检索」这一步。"
        ),
    ),
    SourceSpec(
        key="wanfang",
        label="万方 · 中文",
        disciplines=ALL_DISCIPLINES,
        rank=5,
        language="zh",
        requires_key="wanfang_app_key",
        min_interval_seconds=0.5,
        note="中文期刊与会议；需要在 .env 配置 WANFANG_APP_KEY 后启用",
    ),
    SourceSpec(
        key="scopus",
        label="Scopus · 国际引文库",
        rank=8,
        language="en",
        requires_key="scopus_api_key",
        min_interval_seconds=0.5,
        note="作者与文献检索；需要 SCOPUS_API_KEY 和对应的 Elsevier 访问权限",
    ),
    SourceSpec(
        key="springer_meta",
        label="Springer Nature · 元数据",
        rank=35,
        language="en",
        requires_key="springer_meta_api_key",
        min_interval_seconds=1.0,
        supports_author_search=False,
        note="出版社范围内的题名与元数据补充，不是全学科作者检索库",
    ),
    SourceSpec(
        key="springer_open_access",
        label="Springer Nature · 开放获取",
        rank=36,
        language="en",
        requires_key="springer_open_access_api_key",
        min_interval_seconds=1.0,
        supports_author_search=False,
        note="Springer Nature 开放获取内容的题名补充，不是全学科作者检索库",
    ),
    SourceSpec(
        key="aminer",
        label="AMiner · 中文与学者画像",
        disciplines=ALL_DISCIPLINES,
        rank=20,
        language="zh",
        requires_key="aminer_api_key",
        cost_per_call=0.01,
        min_interval_seconds=0.5,
        enabled=False,
        note="可选增强源；按次计费，需要 AMINER_API_KEY，默认关闭",
    ),
)


def config_path(root: Path | None = None) -> Path:
    """用户可编辑的配置文件位置（仓库根目录下的 config/sources.json）。"""
    if root is not None:
        return root / CONFIG_RELATIVE_PATH
    return Path(__file__).resolve().parents[3] / CONFIG_RELATIVE_PATH


def _load_json(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _apply_override(base: SourceSpec, override: dict) -> SourceSpec:
    allowed = {f for f in SourceSpec.__dataclass_fields__ if f != "key"}
    changes = {k: v for k, v in override.items() if k in allowed}
    if "disciplines" in changes and isinstance(changes["disciplines"], list):
        changes["disciplines"] = tuple(str(item) for item in changes["disciplines"])
    try:
        return replace(base, **changes)
    except TypeError:  # 配置写错类型时忽略这条覆盖，保住默认行为
        return base


def load_sources(path: Path | None = None) -> tuple[SourceSpec, ...]:
    """加载检索源表：内置默认 + config/sources.json 覆盖。配置坏了就用默认值。"""
    target = path or config_path()
    data = _load_json(target)
    entries = data.get("sources")
    if not isinstance(entries, list):
        return DEFAULT_SOURCES

    result = list(DEFAULT_SOURCES)
    index = {spec.key: position for position, spec in enumerate(result)}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        if key in index:
            position = index[key]
            result[position] = _apply_override(result[position], entry)
        else:
            label = str(entry.get("label") or key)
            result.append(_apply_override(SourceSpec(key=key, label=label), entry))
    return tuple(result)


def load_discipline_keywords(path: Path | None = None) -> dict[str, list[str]]:
    """学科关键词表：内置默认 + config/sources.json 覆盖（整段替换）。"""
    data = _load_json(path or config_path())
    table = data.get("discipline_keywords")
    if not isinstance(table, dict):
        return DEFAULT_DISCIPLINE_KEYWORDS
    result: dict[str, list[str]] = {}
    for key, words in table.items():
        if isinstance(words, list):
            result[str(key)] = [str(word) for word in words if str(word).strip()]
    return result or DEFAULT_DISCIPLINE_KEYWORDS


def available_sources(
    specs: tuple[SourceSpec, ...] | None = None,
    *,
    key_values: dict[str, str] | None = None,
) -> tuple[list[SourceSpec], list[SourceSpec]]:
    """把源分成「本次可用」和「缺 Key 被跳过」两拨（跳过不是失败，要告诉用户原因）。"""
    key_values = key_values or {}
    usable: list[SourceSpec] = []
    skipped: list[SourceSpec] = []
    for spec in specs or DEFAULT_SOURCES:
        if not spec.enabled:
            continue
        if spec.requires_key and not key_values.get(spec.requires_key):
            skipped.append(spec)
            continue
        usable.append(spec)
    return usable, skipped


def classify_sources(
    discipline: str | None,
    *,
    specs: tuple[SourceSpec, ...] | None = None,
    key_values: dict[str, str] | None = None,
    language: str = "auto",
) -> tuple[list[SourceSpec], list[tuple[SourceSpec, str]]]:
    """挑出本次要查的源，并给出「没查的原因」。

    返回 (要查的源, [(被跳过的源, 原因)])，原因取值：
    - `disabled`：源在配置里被关掉（可能因为对方有反爬，见 spec.note）；
    - `no_key`：需要 Key 但用户没配；
    - `language`：用户限定了语言（如"仅中文"），该源语言不符。
    """
    key_values = key_values or {}
    usable, no_key = available_sources(specs, key_values=key_values)
    selected: list[SourceSpec] = []
    notes: list[tuple[SourceSpec, str]] = [(spec, "no_key") for spec in no_key]

    for spec in specs or DEFAULT_SOURCES:
        if not spec.enabled:
            notes.append((spec, "disabled"))

    for spec in usable:
        if language in ("zh", "en"):
            if spec.language != language:
                notes.append((spec, "language"))
                continue
        if not spec.covers(discipline):
            continue
        selected.append(spec)

    selected.sort(key=lambda spec: (spec.rank, spec.key))
    return selected, notes
