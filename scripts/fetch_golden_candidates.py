"""抓取消歧评测 golden set 的候选论文（一次性工具，用于扩充 evals/disambiguation_golden.jsonl）。

用法：python scripts/fetch_golden_candidates.py
输出：每行一个 (name, institution) 的候选论文 JSON 到 stdout，供人工标注 expected 字段。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from advisor_fit.config import settings
from advisor_fit.providers.wanfang import WanfangProvider

PROFESSORS = [
    ("陆伟", "武汉大学"),
    ("李伟", "武汉大学"),
    ("张伟", "北京大学"),
    ("王伟", "清华大学"),
    ("刘伟", "中国人民大学"),
    ("陈伟", "上海交通大学"),
    ("杨伟", "浙江大学"),
    ("赵伟", "南京大学"),
    ("李超", "华中科技大学"),
    ("王芳", "武汉大学"),
]

provider = WanfangProvider(settings.wanfang_app_key)
for name, institution in PROFESSORS:
    works = provider.search_publications(name, institution=institution, limit=15)
    candidates = [
        {
            "title": w.title,
            "institution": w.institution or "",
            "authors": w.authors,
            "year": w.year,
            "abstract": (w.abstract or "")[:200],
        }
        for w in works
    ]
    print(
        json.dumps(
            {"name": name, "institution": institution, "candidates": candidates},
            ensure_ascii=False,
        )
    )
