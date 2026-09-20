"""在线 Demo 的入口：先准备好虚构演示数据，再跑真正的应用。

为什么需要这个文件（而不是直接部署 `app.py`）：

1. **公开演示站不能出现任何真实数据**。这个入口把数据目录指向一个独立的
   `data/demo-data/`，并在首次启动时自动生成一份**全部虚构**的演示数据；
2. 有些托管平台（例如 Streamlit Community Cloud）需要指定一个入口文件，
   而这个文件正好也是"演示站该怎么启动"的唯一说明；
3. 环境变量必须在导入应用之前设置好——配置是在导入时读取的，顺序错了就会
   读到真实数据目录。

本地同样可以这样启动：

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEMO_DATA_DIR = ROOT / "data" / "demo-data"
DEMO_UPLOADS_DIR = ROOT / "data" / "demo-uploads"

# ⚠️ 必须放在任何 advisor_fit 导入之前：Settings 是导入时构造的
os.environ.setdefault("DATA_DIR", str(DEMO_DATA_DIR))
os.environ.setdefault("UPLOADS_DIR", str(DEMO_UPLOADS_DIR))

from advisor_fit.demo_data import ensure_demo_data  # noqa: E402

_summary = ensure_demo_data(os.environ["DATA_DIR"])
if _summary["created"]:
    print(
        f"[demo] 已生成演示数据：{_summary['advisors']} 位虚构导师、"
        f"{_summary['runs']} 条研究记录 → {_summary['data_dir']}"
    )
else:
    print(f"[demo] 复用已有演示数据：{_summary['data_dir']}")

# 用 runpy 执行真正的应用，等同于 `streamlit run app.py`
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
