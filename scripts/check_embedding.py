"""自检向量（embedding）后端：确认配置是否真的接通，以及它到底能不能分辨语义。

用法：

```powershell
# 1) 什么都不配，看默认档长什么样
.\\.venv\\Scripts\\python.exe scripts\\check_embedding.py

# 2) 在 .env 里配好之后再看一次
#    EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
#    EMBEDDING_MODEL=BAAI/bge-m3
#    EMBEDDING_API_KEY=sk-xxxx
.\\.venv\\Scripts\\python.exe scripts\\check_embedding.py
```

它会做三件事：

1. 报出当前用的是哪一档后端（离线档 / 本机模型 / API），以及它是不是真语义；
2. **真的调一次接口**（离线档则是本地算），确认能拿到向量；
3. 用三组对照句检验区分度：**同义句应当高分、无关句应当低分**。

第 3 步是重点：很多接口能返回向量，但配错了模型或维度，相似度就没有区分度，
接上去等于没用。这里直接把它量出来。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from advisor_fit.providers.embedding import build_embedder, cosine  # noqa: E402

# (查询, 真正相关的句子, 无关的句子)
CASES: tuple[tuple[str, str, str], ...] = (
    ("知识图谱", "图谱的构建与表示学习", "油画创作中的色彩运用"),
    ("图神经网络与推荐系统", "GNN 在推荐场景的应用", "宋代瓷器的断代方法"),
    ("数字人文与古籍数字化", "地方志文本的数字化整理", "高温合金的疲劳裂纹扩展"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="自检向量后端")
    parser.add_argument("--save", default="", help="把结果写成 JSON（可选）")
    args = parser.parse_args()

    embedder = build_embedder()
    print("=" * 74)
    print(f"后端：{embedder.name}")
    print(f"是不是真语义：{'是' if embedder.semantic else '否（只做表层相似）'}")
    print(f"理由门槛 {embedder.reason_threshold} ／ 召回门槛 {embedder.recall_threshold}")
    reason = getattr(embedder, "fallback_reason", "")
    if reason:
        print()
        print(f"⚠ 已从别的档降级到离线档 —— {reason}")
        print("  本地模型下不动时，可以先用本脚本把文件直接下到项目内：")
        print("    python scripts/download_local_model.py --model BAAI/bge-small-zh-v1.5")
    if not embedder.semantic:
        print()
        print("提示：当前是内置离线档，它认不出同义词。要真语义，请在 .env 里配置：")
        print("  EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1   # 换成你用的服务")
        print("  EMBEDDING_MODEL=BAAI/bge-m3")
        print("  EMBEDDING_API_KEY=sk-xxxx")
        print("注意：DeepSeek 目前不提供 embedding 接口，它的 Key 配这里不生效。")
    print("=" * 74)

    # 先确认能真的拿到向量
    try:
        probe = embedder.embed(["连通性测试"])
    except Exception as exc:  # noqa: BLE001 - 这里就是要如实报错给用户看
        print(f"\n[失败] 调用向量服务出错：{type(exc).__name__}: {exc}")
        return 1
    if not probe or not probe[0]:
        print("\n[失败] 服务返回了空向量——检查模型名是否写对")
        return 1
    print(f"\n[通过] 拿到向量，维度 {len(probe[0])}\n")

    # 再量区分度
    print(f"{'查询':<24}{'相关':>9}{'无关':>9}{'区分度':>9}   判定")
    print("-" * 74)
    good = 0
    for query, related, unrelated in CASES:
        vectors = embedder.embed([query, related, unrelated])
        near = cosine(vectors[0], vectors[1])
        far = cosine(vectors[0], vectors[2])
        margin = near - far
        ok = margin > 0.05
        good += 1 if ok else 0
        print(
            f"{query:<24}{near:>9.3f}{far:>9.3f}{margin:>+9.3f}   "
            f"{'可以分辨' if ok else '几乎分不开'}"
        )

    print("-" * 74)
    print(f"\n{good}/{len(CASES)} 组能分清「相关」与「无关」。")
    if good < len(CASES):
        print("若有分不开的：多半是模型选得不对（例如用了非中文模型），或接口返回的是")
        print("同一批常量向量。换一个中文/多语言模型再试。")

    if args.save:
        import json

        Path(args.save).write_text(
            json.dumps(
                {
                    "backend": embedder.name,
                    "semantic": embedder.semantic,
                    "dim": len(probe[0]),
                    "cases": [
                        {"query": q, "related": r, "unrelated": u}
                        for q, r, u in CASES
                    ],
                    "passed": good,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n结果已写入 {args.save}")

    return 0 if good == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
