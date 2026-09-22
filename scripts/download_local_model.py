"""把 HuggingFace 模型文件直接下到本地目录，绕开 hf_hub 的符号链接探测。

为什么需要这个：`huggingface_hub` 在建缓存时会先做一次"符号链接支持探测"——
在 snapshots 目录下建临时目录、往里写一个探针文件。这一步在受限环境（沙箱、
某些企业策略）下会被拒绝，报 `PermissionError`，导致模型根本下不来。

而 `SentenceTransformer` 本来就能直接从**本地目录**加载，所以把文件下齐即可：

```powershell
.\\.venv\\Scripts\\python.exe scripts/download_local_model.py --model BAAI/bge-small-zh-v1.5
# 然后在 .env 里写：
#   EMBEDDING_LOCAL_MODEL=.models/bge-small-zh-v1.5
```

这样也顺带解决了国内网络问题：模型落在项目内，不依赖 HF 的缓存位置。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 一个句向量模型跑起来实际需要这些（少了任何一个都加载不了）
WANTED_SUFFIXES = (
    "config.json",
    "model.safetensors",
    "pytorch_model.bin",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
    "modules.json",
    "sentence_bert_config.json",
    "config_sentence_transformers.json",
)
WANTED_PREFIXES = ("1_Pooling/", "2_Dense/", "sentence_bert_config.json")


def _wanted(name: str) -> bool:
    if name in WANTED_SUFFIXES:
        return True
    return any(name.startswith(prefix) for prefix in WANTED_PREFIXES)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 HF 模型下到本地目录")
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--out", default="", help="输出目录，默认 .models/<模型名>")
    parser.add_argument("--endpoint", default="https://huggingface.co",
                        help="镜像地址，国内可用 https://hf-mirror.com")
    args = parser.parse_args()

    import httpx

    out_dir = Path(args.out) if args.out else ROOT / ".models" / args.model.split("/")[-1]
    out_dir.mkdir(parents=True, exist_ok=True)

    base = f"{args.endpoint.rstrip('/')}/{args.model}/resolve/main"
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        listing = client.get(f"{args.endpoint.rstrip('/')}/api/models/{args.model}")
        listing.raise_for_status()
        files = [item["rfilename"] for item in listing.json().get("siblings", [])]
        targets = [name for name in files if _wanted(name)]
        if not targets:
            print(f"[失败] 模型文件列表为空：{files[:10]}")
            return 1

        print(f"模型 {args.model}，需要 {len(targets)} 个文件 -> {out_dir}")
        for name in targets:
            dest = out_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size > 0:
                print(f"  跳过（已存在） {name}")
                continue
            with client.stream("GET", f"{base}/{name}") as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                written = 0
                with dest.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        handle.write(chunk)
                        written += len(chunk)
                print(f"  已下载 {name}  ({written / 1e6:.1f} MB"
                      + (f" / {total / 1e6:.1f} MB" if total else "") + ")")

    manifest = out_dir / "_download_manifest.json"
    manifest.write_text(
        json.dumps({"model": args.model, "endpoint": args.endpoint, "files": targets},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    print(f"\n[完成] {out_dir}  合计 {size:.1f} MB")
    print("\n在 .env 里写一行即可启用：")
    print(f"  EMBEDDING_LOCAL_MODEL={out_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
