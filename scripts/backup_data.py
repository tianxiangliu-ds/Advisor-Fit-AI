"""数据备份：把本地数据打包成一个 zip，方便换电脑或误删后恢复。

**默认什么不会进备份包**（这是刻意的，data/ 里有些东西不该随手拷走）：

- `.env`：里面有你的 API Key。要一起备份必须显式加 `--include-env`；
- `uploads/`：里面有你的真实简历。要一起备份必须显式加 `--include-uploads`；
- 三个大库（advisors / faculty / supervisor_roster）：加起来上百 MB，
  随时可以用采集脚本重建，默认不打包；加 `--all` 才包含。

用法：

```powershell
# 默认：只备份运行数据（研究记录、网页缓存）
.\\.venv\\Scripts\\python.exe scripts\\backup_data.py

# 连大库一起备份
.\\.venv\\Scripts\\python.exe scripts\\backup_data.py --all

# 指定输出目录
.\\.venv\\Scripts\\python.exe scripts\\backup_data.py --out D:\\backup
```

备份包里会写一个 `MANIFEST.md`，说明里面有什么、怎么恢复。
"""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="备份本地数据")
    parser.add_argument("--out", default=str(ROOT / "backups"), help="输出目录")
    parser.add_argument("--prefix", default="advisor-fit", help="文件名前缀")
    parser.add_argument("--all", action="store_true", help="连大库一起备份（体积大）")
    parser.add_argument("--include-uploads", action="store_true", help="连上传的简历一起备份")
    parser.add_argument("--include-env", action="store_true", help="连 .env 一起备份（含 Key！）")
    args = parser.parse_args()

    from advisor_fit import __version__
    from advisor_fit.storage.backup import (
        collect_entries,
        default_backup_name,
        manifest_text,
        write_backup,
    )

    entries, missing, skipped = collect_entries(
        ROOT,
        include_big=args.all,
        include_uploads=args.include_uploads,
        include_env=args.include_env,
    )
    if not entries:
        print("[失败] 没有可备份的数据（data/ 目录下没有数据库文件）。")
        return 1

    target = Path(args.out) / default_backup_name(args.prefix)
    write_backup(target, entries, manifest_text(entries, skipped, version=__version__))
    size_mb = target.stat().st_size / 1024 / 1024
    print(f"[完成] 备份已生成：{target}（{size_mb:.1f} MB）")
    print(f"       包含 {len(entries)} 个文件；未找到：" + ("、".join(missing) or "无"))
    if not args.include_env:
        print("       备份包不含 .env —— 恢复后需要重新填 API Key。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
