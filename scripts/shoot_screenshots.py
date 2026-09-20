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
    """点侧栏导航按钮。

    用 `data-testid="stSidebar"` 限定在侧栏里找，并且每页都从干净状态重新加载后再点，
    避免上一页的交互（例如下拉框）留下状态导致点击失效。
    """
    sidebar = page.get_by_test_id("stSidebar")
    button = sidebar.get_by_role("button").filter(has_text=label).first
    try:
        button.click(timeout=10000)
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

    # 勾选前两位候选，让「并排比较」出现在页面上。
    # 注意：Streamlit 的下拉标签不在 stMultiSelect 容器内部，所以按序号取（第 2 个下拉框）。
    picker = page.locator('[data-testid="stMultiSelect"]').nth(1)
    picker.click()
    settle(page, 1.2)
    opened = page.locator('[role="option"]')
    for index in range(min(2, opened.count())):
        opened.nth(index).click()
        time.sleep(0.5)
    page.keyboard.press("Escape")
    settle(page, 2.5)


def fill_professor_form(page) -> None:
    """把导师档案页的必填字段填上，截图才看得出这一页长什么样。"""
    values = {
        "导师姓名（必填）": "示例导师",
        "学校/单位（必填）": "示例大学",
        "院系（可选）": "信息管理学院",
        "职称（可选）": "教授",
    }
    for label, value in values.items():
        container = page.locator('[data-testid="stTextInput"]').filter(has_text=label).first
        try:
            container.locator("input").fill(value, timeout=5000)
        except Exception as exc:  # noqa: BLE001
            print(f"  [警告] 填不了「{label}」：{exc}")
    interests = (
        page.locator('[data-testid="stTextArea"]').filter(has_text="官网公开研究方向").first
    )
    try:
        interests.locator("textarea").fill("知识图谱；数字人文；文化遗产数字化", timeout=5000)
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 填不了研究方向：{exc}")
    settle(page, 2.0)


def open_first_history_record(page) -> bool:
    """在「研究档案」页点开第一条记录，截到研究匹配简报。

    记录按钮的文字形如「示例导师 · 示例大学」，用中点做特征来定位，
    避免依赖 Streamlit 内部的主区域容器名。
    """
    button = page.locator('button:has-text("·")').first
    try:
        button.click(timeout=8000)
        settle(page, 3.0)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 打不开历史记录：{exc}")
        return False


def shoot_agent_trace(page, out_dir: Path) -> bool:
    """单独把「Agent 运行轨迹」区截一张。

    轨迹是"Agent 可见"的核心展示，也是 README 与对外演示里最该出现的一张图；
    它通常在报告下方，需要先滚动到该元素再截。
    """
    panel = page.locator(".trace-panel").first
    try:
        panel.scroll_into_view_if_needed(timeout=8000)
        time.sleep(1.2)
        target = out_dir / "08-agent-trace.png"
        panel.screenshot(path=str(target))
        print(f"  已保存 {target}")
        return True
    except Exception as exc:  # noqa: BLE001 - 截不到不该影响整轮截图
        print(f"  [警告] 截不到 Agent 轨迹区：{exc}")
        return False


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

        for key, label, filename in pages:
            print(f"→ {label}")
            page.goto(args.url, wait_until="domcontentloaded")
            settle(page, 4.0)
            click_sidebar(page, label)
            settle(page)
            if key == "direction":
                try:
                    fill_direction_search(page)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [警告] 方向检索交互失败：{exc}")
            elif key == "professor":
                fill_professor_form(page)
            page.screenshot(path=str(out_dir / filename))
            print(f"  已保存 {out_dir / filename}")

            # 研究档案页额外截两张：点开某条记录后的研究简报 + Agent 运行轨迹
            if key == "history" and open_first_history_record(page):
                brief = out_dir / "07-brief.png"
                page.screenshot(path=str(brief))
                print(f"  已保存 {brief}")
                shoot_agent_trace(page, out_dir)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
