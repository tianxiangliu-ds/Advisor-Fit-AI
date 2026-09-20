"""统一评测：一键跑出各维度指标，并能与基线对比防回归。

对应产品愿景的第四根支柱——从**实体消歧准确性 / Evidence 覆盖率 / 匹配一致性 /
LLM 输出可靠性**四个维度评估并迭代 Agent Workflow。

三条设计原则：

1. **离线优先**：不配 LLM Key、不联网也能跑出大部分指标（规则基线 + 固定样本）。
   需要 Key 的指标标成「跳过」并写明原因，而不是让整轮评测失败——这与
   `CLAUDE.md` 的「默认主干必须免费免 Key」一致。
2. **可对比**：结果能存成基线；下次跑完自动报出每个指标的变化方向。
3. **该高的变低就是回归**：每个指标声明 `higher_is_better` 与及格线，
   比较时不看绝对值，只看方向与门线，避免"数字好看但退步了"。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from advisor_fit.agents.research import disambiguate_papers
from advisor_fit.analysis.matching import build_match_report
from advisor_fit.llm.prompts import _REGISTRY
from advisor_fit.llm.provider import NullLLM
from advisor_fit.models.common import FactStatus
from advisor_fit.models.evidence import Claim, Evidence
from advisor_fit.models.professor import ObservedTopic, ProfessorProfile
from advisor_fit.models.student import StudentFact, StudentProfile
from advisor_fit.validation.claims import validate_claims

# 二分类口径：match 与 review 都算「是本人」（正例），exclude 算「非本人」
_POSITIVE_LABELS = ("match", "review")


@dataclass(frozen=True)
class Metric:
    """一个可比较的指标。"""

    key: str
    label: str
    value: float
    unit: str = ""  # "" 原样 / "%" / "count"
    higher_is_better: bool = True
    gate: float | None = None  # 及格线，None 表示只看方向
    detail: str = ""

    @property
    def passed(self) -> bool:
        if self.gate is None:
            return True
        return self.value >= self.gate if self.higher_is_better else self.value <= self.gate

    def display(self) -> str:
        if self.unit == "%":
            return f"{self.value * 100:.1f}%"
        if self.unit == "count":
            return f"{self.value:.0f}"
        return f"{self.value:.3f}"


@dataclass
class EvalReport:
    """一次评测的完整结果。"""

    generated_at: str = ""
    metrics: list[Metric] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def get(self, key: str) -> Metric | None:
        for metric in self.metrics:
            if metric.key == key:
                return metric
        return None

    def values(self) -> dict[str, float]:
        return {metric.key: metric.value for metric in self.metrics}

    @property
    def gates_passed(self) -> bool:
        return all(metric.passed for metric in self.metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "metrics": [asdict(metric) | {"passed": metric.passed} for metric in self.metrics],
            "skipped": list(self.skipped),
            "gates_passed": self.gates_passed,
        }


# -- 各维度评测 ----------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def eval_disambiguation(root: Path, *, llm=None) -> list[Metric]:
    """实体消歧准确性：precision / recall / F1。

    `llm` 传 NullLLM 走机构规则基线（离线、确定性）；不传则用当前配置的模型。
    规则基线的意义是**下限**：LLM 再差也不该比规则差。
    """
    cases = _load_jsonl(root / "evals" / "disambiguation_golden.jsonl")
    if not cases:
        return []

    tp = fp = fn = 0
    for case in cases:
        papers = [
            {
                "title": cand.get("title", ""),
                "authors": cand.get("authors", []),
                "institution": cand.get("institution", ""),
                "year": cand.get("year"),
                "abstract": cand.get("abstract", ""),
                "belongs": True,
                "needs_review": False,
                "disambig_reason": "",
            }
            for cand in case.get("candidates", [])
        ]
        judged = disambiguate_papers(
            llm if llm is not None else NullLLM(),
            papers,
            professor_name=case.get("name", ""),
            institution=case.get("institution"),
        )
        for paper, cand in zip(judged, case.get("candidates", []), strict=False):
            predicted_positive = bool(paper.get("belongs", True))
            actually_positive = cand.get("expected") in _POSITIVE_LABELS
            if predicted_positive and actually_positive:
                tp += 1
            elif predicted_positive and not actually_positive:
                fp += 1
            elif not predicted_positive and actually_positive:
                fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    detail = f"{len(cases)} 位导师 / tp={tp} fp={fp} fn={fn}"
    # 规则基线只保召回：它不排除任何论文，把机构对不上的标成"需人工复核"，
    # 所以 precision 恰好等于金标里的正例比例，是**下限而非成绩**。
    # 因此只给召回设门线；precision / F1 作为观察值记录，提精度是 LLM 与人工确认的活。
    return [
        Metric("disambig.precision", "消歧准确率（规则基线，观察值）",
               precision, "%", True, None, detail),
        Metric("disambig.recall", "消歧召回率（规则基线）",
               recall, "%", True, 0.9, detail),
        Metric("disambig.f1", "消歧 F1（规则基线，观察值）",
               f1, "%", True, None, detail),
    ]


def eval_claims(root: Path) -> list[Metric]:
    """Evidence 覆盖率：Claim–Evidence 校验与金标一致的比例。"""
    cases = _load_jsonl(root / "evals" / "gold_claims.jsonl")
    if not cases:
        return []

    agree = 0
    unsupported_expected = 0
    for case in cases:
        claim = Claim(**case["claim"])
        evidences = {e["id"]: Evidence(**e) for e in case["evidences"]}
        result = validate_claims([claim], evidences)
        if bool(result.ok) == bool(case["expected_valid"]):
            agree += 1
        if not case["expected_valid"]:
            unsupported_expected += 1

    accuracy = agree / len(cases)
    return [
        Metric(
            "claims.accuracy", "Claim–Evidence 判定一致率",
            accuracy, "%", True, 1.0, f"{agree}/{len(cases)} 条与金标一致",
        ),
        Metric(
            "claims.unsupported_detected", "无证据声明被拦下数",
            float(unsupported_expected), "count", True, None,
            "金标里标注为「不应通过」的条数（应被拦住）",
        ),
    ]


def eval_matching() -> list[Metric]:
    """匹配一致性：同样输入结果必须一致；交集多的不该比交集少的得分低。"""
    student = StudentProfile(
        student_id="s1",
        facts=[
            StudentFact(id="f1", field="skill", value="Python",
                        status=FactStatus.FACT, user_confirmed=True),
            StudentFact(id="f2", field="interest", value="信息检索",
                        status=FactStatus.FACT, user_confirmed=True),
        ],
    )
    professor = ProfessorProfile(
        professor_id="p1",
        identity_confirmed=True,
        declared_interests=[ObservedTopic(topic="信息检索", trend="DECLARED")],
        observed_recent_topics=[ObservedTopic(topic="检索增强生成", trend="EMERGING")],
    )
    unrelated = StudentProfile(
        student_id="s1",
        facts=[StudentFact(id="f9", field="skill", value="油画",
                           status=FactStatus.FACT, user_confirmed=True)],
    )

    first = build_match_report(student, professor)
    second = build_match_report(student, professor)
    deterministic = 1.0 if first.model_dump() == second.model_dump() else 0.0

    # 单调性判据用 gaps 而不是 strengths：
    # strengths 里混着"技能覆盖""证据充分度"这类与研究方向无关的通用条目，
    # 两份输入都会拿到同样条数，拿它比较是无效的。
    # 真正随交集变化的是 gaps——没有交集时会多出「未覆盖：XXX」。
    related_gaps = len(first.gaps)
    unrelated_gaps = len(build_match_report(unrelated, professor).gaps)
    monotonic = 1.0 if unrelated_gaps > related_gaps else 0.0

    return [
        Metric("match.determinism", "匹配结果确定性", deterministic, "", True, 1.0,
               "同一份输入连跑两次必须完全一致"),
        Metric("match.monotonic", "匹配单调性", monotonic, "", True, 1.0,
               f"无交集的缺口数 {unrelated_gaps} 必须多于有交集的 {related_gaps}"),
    ]


def eval_advisor_library(root: Path) -> tuple[list[Metric], list[dict[str, str]]]:
    """导师库的数据质量：界面词残留与真人存活。

    这两条都是**事故驱动**的指标，不是凑数的：
    - `ui_word_residue`：库里不该存在"师资队伍""校园风光"这类界面词。它们混进来
      说明采集或清洗漏了；
    - `must_survive_missing`：`MUST_SURVIVE_NAMES` 里的真人一个都不能少。
      2026-09 的清洗事故删掉了 13 条真人（含清华教授张学工），当时**没有任何
      自动检查能发现**——只有人工逐条复核才看出来。这两条指标就是为了让那种事
      下次自己冒出来。

    没有库文件时（新克隆、演示数据尚未生成）跳过并说明，不算失败。
    """
    db = root / "data" / "advisors.db"
    if not db.exists():
        return [], [{"key": "library", "reason": "还没有导师库（data/advisors.db），跳过"}]

    import sqlite3

    from advisor_fit.ingest.name_verify import MUST_SURVIVE_NAMES, is_safe_to_auto_delete

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return [], [{"key": "library", "reason": f"打不开导师库：{exc}"}]
    try:
        rows = list(conn.execute("SELECT name, title, email, research_directions FROM advisors"))
    except sqlite3.Error as exc:
        return [], [{"key": "library", "reason": f"读不了导师库：{exc}"}]
    finally:
        conn.close()

    total = len(rows)
    if not total:
        return [Metric("library.advisors", "导师库条目", 0.0, "count", True, None)], []

    ui_residue = sum(1 for name, *_ in rows if is_safe_to_auto_delete(name or ""))
    present = {name for name, *_ in rows}
    missing = [name for name in MUST_SURVIVE_NAMES if name not in present]

    filled = 0
    for _, title, email, directions in rows:
        # 库里空列表存成字符串 '[]'，而 '[]' 在 Python 里是真值——
        # 不显式判空的话这个指标会永远显示 100%，等于没测。
        if any(str(value or "").strip() not in ("", "[]", "{}", "null") for value in
               (title, email, directions)):
            filled += 1

    return [
        Metric("library.advisors", "导师库条目", float(total), "count", True, None,
               "数字只作记录，不设门线（取决于采集进度）"),
        Metric("library.ui_word_residue", "导师库界面词残留", float(ui_residue),
               "count", False, 0.0,
               "库里不该出现「师资队伍」这类界面词；出现说明采集或清洗漏了"),
        Metric("library.must_survive_missing", "被误删的真人", float(len(missing)),
               "count", False, 0.0,
               ("全部在库" if not missing else "缺失：" + "、".join(missing))),
        Metric("library.field_fill_rate", "导师库字段有值率",
               filled / total, "%", True, None,
               "职称/邮箱/研究方向至少有一项的比例（观察值，取决于采集覆盖）"),
    ], []


def eval_prompts() -> list[Metric]:
    """LLM 输出可靠性（离线代理指标）：提示词必须登记且非空、带版本。"""
    total = len(_REGISTRY)
    usable = 0
    for prompt in _REGISTRY.values():
        text = getattr(prompt, "text", "") or ""
        version = getattr(prompt, "version", "") or ""
        if text.strip() and version.strip():
            usable += 1
    return [
        Metric("prompts.registered", "已登记提示词", float(total), "count", True, 10.0,
               "提示词集中在 llm/prompts.py 管理，不在业务代码里散落"),
        Metric("prompts.usable", "提示词可用率",
               (usable / total) if total else 0.0, "%", True, 1.0,
               f"{usable}/{total} 条非空且带版本号"),
    ]


def run_all(root: Path, *, llm=None, include_llm: bool = False) -> EvalReport:
    """跑全部离线指标；`include_llm=True` 时额外跑需要 Key 的消歧评测。"""
    report = EvalReport(generated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    report.metrics.extend(eval_disambiguation(root, llm=NullLLM()))
    report.metrics.extend(eval_claims(root))
    report.metrics.extend(eval_matching())
    report.metrics.extend(eval_prompts())
    library_metrics, library_skipped = eval_advisor_library(root)
    report.metrics.extend(library_metrics)
    report.skipped.extend(library_skipped)

    if include_llm and llm is not None and not isinstance(llm, NullLLM):
        llm_metrics = eval_disambiguation(root, llm=llm)
        report.metrics.extend(
            Metric(
                key=f"llm.{m.key}", label=f"{m.label}（真实模型）", value=m.value,
                unit=m.unit, higher_is_better=m.higher_is_better, gate=m.gate,
                detail=m.detail,
            )
            for m in llm_metrics
        )
    else:
        report.skipped.append({
            "key": "llm.disambig",
            "reason": "未配置 LLM_API_KEY，真实模型消歧评测跳过（规则基线已跑）",
        })
    return report


# -- 基线对比 ------------------------------------------------------------------


def save_baseline(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_baseline(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        item["key"]: float(item["value"])
        for item in payload.get("metrics", [])
        if "key" in item and "value" in item
    }


def compare_to_baseline(report: EvalReport, baseline: dict[str, float]) -> list[dict[str, Any]]:
    """逐指标对比基线。返回每个指标的变化；`regressed=True` 表示退步了。"""
    changes: list[dict[str, Any]] = []
    for metric in report.metrics:
        if metric.key not in baseline:
            changes.append({
                "key": metric.key, "label": metric.label,
                "before": None, "after": metric.value,
                "delta": None, "regressed": False, "new": True,
            })
            continue
        before = baseline[metric.key]
        delta = metric.value - before
        if abs(delta) < 1e-9:
            regressed = False
        elif metric.higher_is_better:
            regressed = delta < 0
        else:
            regressed = delta > 0
        changes.append({
            "key": metric.key, "label": metric.label,
            "before": before, "after": metric.value,
            "delta": delta, "regressed": regressed, "new": False,
        })
    return changes
