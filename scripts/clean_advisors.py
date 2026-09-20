"""清洗导师数据：删掉被误当成人名的界面词。

数据库里的错误来自两处：官网采集（早期过滤太松）和社区名册（本身就混着栏目名）。
本脚本做两轮：

**第一轮 · 规则（可离线）**：只删"绝不可能出现在姓名里"的界面词根命中的条目
（服务/下载/师资/学院/简介/概况…）。像「新闻」「文化」「方向」这类词根**故意不用**，
因为「郭新闻」「李文化」「方向忠」都是真实存在的教授姓名。

**第二轮 · 大模型（需要 LLM_API_KEY）**：把规则拿不准的条目交给大模型逐条判定，
区分「真人名（含罕见姓氏、外籍学者、带职称后缀）」与「栏目名/界面词」，
再删掉模型确认不是人名的部分。没有 Key 时自动跳过这一轮。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\clean_advisors.py            # 预演
    .\\.venv\\Scripts\\python.exe scripts\\clean_advisors.py --apply    # 执行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from advisor_fit.config import settings  # noqa: E402
from advisor_fit.ingest.name_verify import (  # noqa: E402
    classify_names_with_llm,
    has_ui_word,
    is_safe_to_auto_delete,
)
from advisor_fit.llm.provider import NullLLM, build_llm  # noqa: E402
from advisor_fit.storage.advisor_repo import AdvisorRepository  # noqa: E402

# 这些真实姓名必须活下来 —— 回归检查用
MUST_SURVIVE = {"郭新闻", "李文化", "方向忠", "闫永达", "玄玉波", "初剑峰"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正执行删除")
    parser.add_argument("--no-llm", action="store_true", help="跳过第二轮大模型判定")
    parser.add_argument("--db", default=str(Path(settings.data_dir) / "advisors.db"))
    args = parser.parse_args()

    repo = AdvisorRepository(Path(args.db))
    before = repo.count()
    advisors = repo.all_advisors()

    # 第一轮：规则
    rule_delete = [a for a in advisors if is_safe_to_auto_delete(a.name)]
    rule_names = {a.name for a in rule_delete}

    print("=" * 76)
    print(f"清洗前导师总数：{before}")
    print()
    print(f"【第一轮 · 规则】命中「绝不可能出现在姓名里」的界面词根：{len(rule_delete)} 条")
    for item in rule_delete[:15]:
        reason = has_ui_word(item.name) or "不符合姓名格式"
        print(f"    {item.university:<12}{item.name:<16}{reason}")
    if len(rule_delete) > 15:
        print(f"    … 还有 {len(rule_delete) - 15} 条")

    # 第二轮：大模型判"拿不准的"
    survivors = [a for a in advisors if a.name not in rule_names]
    llm_delete: list = []
    llm = NullLLM() if args.no_llm else build_llm()
    if isinstance(llm, NullLLM):
        print()
        print("【第二轮 · 大模型】未配置 LLM_API_KEY，跳过（规则拿不准的条目一律保留）")
    else:
        candidates = sorted({a.name for a in survivors})
        print()
        print(f"【第二轮 · 大模型】把 {len(candidates)} 个「规则拿不准」的姓名交给模型判定…")
        keep = classify_names_with_llm(llm, candidates)
        if keep is None:
            print("    模型不可用，本轮回退为「全部保留」")
        else:
            llm_delete = [a for a in survivors if a.name not in keep]
            print(f"    模型确认是真人名：{len(keep)} 个")
            print(f"    判定不是人名、将删除：{len(llm_delete)} 条")
            for item in llm_delete[:20]:
                print(f"      {item.university:<12}{item.name}")

    to_delete = rule_delete + llm_delete
    delete_names = {a.name for a in to_delete}
    lost = MUST_SURVIVE & delete_names
    print()
    if lost:
        print(f"⚠️  警告：以下真实姓名被误判，将不会删除它们：{sorted(lost)}")
        to_delete = [a for a in to_delete if a.name not in lost]

    print(f"合计待删除：{len(to_delete)} 条")
    if args.apply and to_delete:
        target = {a.name for a in to_delete}
        removed, _ = repo.delete_by_name_rule(lambda name: name in target)
        print(f"已删除 {removed} 条；清洗后导师总数：{repo.count()}")
    elif not args.apply:
        print("（预演模式，未做修改；确认无误后加 --apply 执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
