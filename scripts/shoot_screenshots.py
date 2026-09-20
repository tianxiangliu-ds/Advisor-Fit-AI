"""给界面截图：对着本地运行的应用逐页截图，用于 README 与文档。

为什么做成仓库脚本：开源项目的截图必须**可复现**，而不是靠某个人手工截一次。
用演示数据（`scripts/seed_demo_data.py` 生成，全部虚构）跑一遍即可重新生成。

前置条件：

```powershell
uv sync --group crawl
.\\.venv\\Scripts\\python.exe -m playwright install chromium

# 1. 生成演示数据
.\\.venv\\Scripts\\python.exe scripts\\seed_demo_data.py --out data\\demo-data

# 2. 用演示数据启动应用（另开一个终端 / 后台运行）
$env:DATA_DIR="data\\demo-data"; $env:UPLOADS_DIR="data\\demo-uploads"
.\\.venv\\Scripts\\python.exe -m streamlit run app.py --server.port 8505 --server.headless true

# 3. 截图（默认输出到 screenshots/）
.\\.venv\\Scripts\\python.exe scripts\\shoot_screenshots.py
```

注意：截图里出现的导师、论文、学校**全部是虚构的演示数据**，
不要拿真实导师或真实论文当素材提交进仓库。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

VIEWPORT = {"width": 1440, "height": 940}
SCALE = 2  # 2 倍图，README 里在高分屏上不糊

# (页面 key, 侧栏按钮文字, 输出文件名)
PAGES: tuple[tuple[str, str, str], ...] = (
    ("home", "首屏 / 项目入口", "01-home.png"),
    ("direction", "方向找导师", "02-direction.png"),
    ("resume", "学生事实", "03-resume.png"),
    ("professor", "导师档案", "04-professor.png"),
    ("papers", "论文核验", "05-papers.png"),
    ("history", "研究档案", "06-history.png"),
)


def settle(page, seconds: float = 2.2) -> None:
    page.wait_for_load_state("networkidle")
    time.sleep(seconds)


def click_sidebar(page, label: str) -> bool:
    button = page.get_by_role("button", name=label, exact=False).first
    try:
        button.click(timeout=8000)
        return True
    except Exception as exc:  # noqa: BLE001 - 某一页点不动不该中断整轮截图
        print(f"  [警告] 点不动「{label}」：{exc}")
        return False


def fill_direction_search(page) -> None:
    """在「方向找导师」页做一次真实检索，并勾选两位做并排比较。"""
    box = page.get_by_placeholder("例如：知识图谱、数字人文、文化遗产").first
    box.click()
    box.fill("知识图谱、数字人文")
    page.get_by_role("button", name="找候选导师", exact=False).first.click()
    settle(page, 3.5)
    combo = page.locator('[data-testid="stMultiSelect"]').nth(1)
    combo.click()
    time.sleep(0.8)
    for option in page.locator('[role="option"]').all()[:2]:
        option.click()
        time.sleep(0.4)
    page.keyboard.press("Escape")
    settle(page, 2.5)


def main() -> int:
    parser = argparse.ArgumentParser(description="给界面截图")
    parser.add_argument("--url", default="http://127.0.0.1:8505", help="应用地址")
    parser.add_argument("--out", default="screenshots", help="输出目录")
    parser.add_argument("--only", default="", help="只截某一页（页面 key）")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[失败] 没装 Playwright：先运行 uv sync --group crawl")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = [page for page in PAGES if not args.only or page[0] == args.only]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE)
        page.goto(args.url, wait_until="domcontentloaded")
        settle(page, 4.0)

        for key, label, filename in pages:
            print(f"→ {label}")
            click_sidebar(page, label)
            settle(page)
            if key == "direction":
                try:
                    fill_direction_search(page)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [警告] 方向检索交互失败：{exc}")
            page.screenshot(path=str(out_dir / filename))
            print(f"  已保存 {out_dir / filename}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
