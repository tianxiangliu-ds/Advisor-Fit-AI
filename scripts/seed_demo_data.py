"""生成一份**虚构的**演示数据，用于截图、在线 Demo 或第一次上手体验。

真正的实现在 `src/advisor_fit/demo_data.py`——在线 Demo 的入口
（`streamlit_app.py`）用的是同一份，两边共用才不会出现"演示站有的、截图里没有"。
这个脚本只是命令行外壳。

```powershell
# 生成到 .tmp/demo-data（默认，不碰你的 data/）
.\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py

# 指定输出目录
.\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py --out data\\demo-data

# 已存在也强制重建
.\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py --out data\\demo-data --force

# 然后用这份数据启动应用
$env:DATA_DIR=".tmp/demo-data"; .\\.venv\\Scripts\\python.exe -m streamlit run app.py
```

数据全部是编造的：学校叫「示例大学」、导师叫「示例导师」、论文标题带「（示例）」。
**不会**使用任何真实人物或真实论文信息。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:  # 允许直接运行脚本而无需安装
    sys.path.insert(0, str(ROOT / "src"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from advisor_fit.demo_data import ensure_demo_data  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成虚构的演示数据")
    parser.add_argument("--out", default=str(ROOT / ".tmp" / "demo-data"), help="输出目录")
    parser.add_argument("--force", action="store_true", help="已存在也重建")
    args = parser.parse_args()

    result = ensure_demo_data(args.out, force=args.force)

    if not result["created"]:
        print(f"[跳过] {result['data_dir']} 已有演示数据（加 --force 可重建）")
    else:
        print(f"[完成] 演示数据已生成：{result['data_dir']}")
    print(f"       导师库 {result['advisors']} 位（全部虚构）；"
          f"已完成研究记录 {result['runs']} 条（含示例 Agent 轨迹）")
    print("       启动方式：")
    print(f'       $env:DATA_DIR="{result["data_dir"]}"; .\\.venv\\Scripts\\python.exe'
          " -m streamlit run app.py")
    print("       提醒：这些数据全是编造的，不要当成真实导师资料使用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
