"""清洗导师数据：先规范化（保人），再删除真正的界面词。

数据库里的错误来自两处：官网采集（早期过滤太松）和社区名册（本身就混着栏目名）。
按顺序做三步，**顺序不能颠倒**：

**第 0 步 · 规范化（保人）**
剥掉粘在姓名上的职称后缀/单位前缀/空格：「董陇军副教授」→「董陇军」、
「工研院胡耀武」→「胡耀武」、「薛 渊」→「薛渊」。与已有条目重名则合并。
> 不先做这一步，后面的删除会把「黄军教授」「李杰老师」这类真人一起删掉。

**第 1 步 · 规则删除（可离线）**
只删"绝不可能出现在姓名里"的界面词根命中的条目（服务/下载/师资/学院/简介/概况…）。
像「新闻」「文化」「方向」这类词根**故意不用**——「郭新闻」「李文化」「方向忠」
都是真实存在的教授姓名。

**第 2 步 · 大模型复核（需要 LLM_API_KEY）**
把规则拿不准的条目交给大模型逐条判定，区分真人名（含罕见姓氏、外籍学者）
与栏目名，再删掉模型确认不是人名的部分。没有 Key 时自动跳过。

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
    has_common_surname,
    has_ui_word,
    is_safe_to_auto_delete,
    looks_like_foreign_name,
    looks_like_person_name,
    normalize_name,
    normalize_person_name,
)
from advisor_fit.llm.provider import NullLLM, build_llm  # noqa: E402
from advisor_fit.storage.advisor_repo import AdvisorRepository  # noqa: E402

# 这些真实姓名必须活下来 —— 回归检查用
MUST_SURVIVE = {"郭新闻", "李文化", "方向忠", "闫永达", "玄玉波", "初剑峰", "黄军", "李杰"}


def needs_llm_review(name: str) -> bool:
    """前置筛选：值不值得送给大模型看。

    全库有三万个名字，全送模型既慢又费钱。这里只挑"规则拿不准"的：
    - 命中宽泛界面词根的（新闻/文化/方向/科学/信息/国际…）；
    - 4 个汉字的（中文姓名多为 2-3 字，4 字值得看一眼）；
    - 首字不在常见姓氏表里的中文名。
    外籍姓名与明显正常的 2-3 字真名直接跳过。
    """
    cleaned = normalize_name(name)
    if not cleaned or looks_like_foreign_name(cleaned):
        return False
    if not looks_like_person_name(cleaned):
        return True
    if has_ui_word(cleaned):
        return True
    if len(cleaned) == 4:
        return True
    return not has_common_surname(cleaned)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正执行改名与删除")
    parser.add_argument("--no-llm", action="store_true", help="跳过第 2 步大模型复核")
    parser.add_argument("--db", default=str(Path(settings.data_dir) / "advisors.db"))
    args = parser.parse_args()

    repo = AdvisorRepository(Path(args.db))
    print("=" * 76)
    print(f"清洗前导师总数：{repo.count()}")

    if not args.apply:
        advisors = repo.all_advisors()
        will_rename = [a for a in advisors if normalize_person_name(a.name) != a.name]
        will_delete = [a for a in advisors if is_safe_to_auto_delete(a.name)]
        print()
        print(f"【第 0 步 · 规范化】将改名 {len(will_rename)} 条，例如：")
        for item in will_rename[:10]:
            print(f"    {item.name}  →  {normalize_person_name(item.name)}")
        print()
        print(f"【第 1 步 · 规则删除】将删除 {len(will_delete)} 条，例如：")
        for item in will_delete[:12]:
            print(f"    {item.university:<12}{item.name:<16}{has_ui_word(item.name) or '格式'}")
        print()
        print("（预演模式，未做修改；确认无误后加 --apply 执行）")
        return 0

    # 第 0 步：规范化
    renamed, merged = repo.normalize_all_names(normalize_person_name)
    print()
    print(f"【第 0 步 · 规范化】改名 {renamed} 条，合并重名 {merged} 条")
    print(f"    规范化后总数：{repo.count()}")

    # 第 1 步：规则删除
    rule_delete = [a for a in repo.all_advisors() if is_safe_to_auto_delete(a.name)]
    rule_names = {a.name for a in rule_delete}
    print()
    print(f"【第 1 步 · 规则删除】命中界面词根 {len(rule_delete)} 条，例如：")
    for item in rule_delete[:12]:
        print(f"    {item.university:<12}{item.name:<16}{has_ui_word(item.name) or '格式'}")

    # 第 2 步：大模型复核
    llm = NullLLM() if args.no_llm else build_llm()
    llm_delete: list = []
    if isinstance(llm, NullLLM):
        print()
        print("【第 2 步 · 大模型】未配置 LLM_API_KEY，跳过")
    else:
        survivors = [a for a in repo.all_advisors() if a.name not in rule_names]
        candidates = sorted({a.name for a in survivors if needs_llm_review(a.name)})
        print()
        print(f"【第 2 步 · 大模型】挑出 {len(candidates)} 个「规则拿不准」的姓名送模型判定…")
        keep = classify_names_with_llm(llm, candidates)
        if keep is None:
            print("    模型不可用，本轮回退为「全部保留」")
        else:
            llm_delete = [a for a in survivors if a.name in candidates and a.name not in keep]
            print(f"    模型确认是真人名：{len(keep)} 个")
            print(f"    判定不是人名、将删除：{len(llm_delete)} 条")
            for item in llm_delete[:15]:
                print(f"      {item.university:<12}{item.name}")

    to_delete = rule_delete + llm_delete
    lost = MUST_SURVIVE & {a.name for a in to_delete}
    if lost:
        print()
        print(f"⚠️  以下真实姓名被误判，将保留不删：{sorted(lost)}")
        to_delete = [a for a in to_delete if a.name not in lost]

    target = {a.name for a in to_delete}
    print()
    print(f"合计待删除：{len(to_delete)} 条")
    if target:
        removed, _ = repo.delete_by_name_rule(lambda name: name in target)
        print(f"已删除 {removed} 条")
    print(f"清洗后导师总数：{repo.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
