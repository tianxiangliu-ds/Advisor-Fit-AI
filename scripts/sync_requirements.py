"""把 `pyproject.toml` 的依赖同步到 `requirements.txt`（给托管平台用）。

Streamlit Community Cloud 之类的平台默认只读 `requirements.txt`，不读 pyproject 的
依赖表。为了不让两边漂移，这里只保留**一处真源**（pyproject），
需要时运行本脚本重新生成，CI 里的测试会检查两边是否一致。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\sync_requirements.py            # 写入
    .\\.venv\\Scripts\\python.exe scripts\\sync_requirements.py --check    # 只检查
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
REQUIREMENTS = ROOT / "requirements.txt"

HEADER = """# 在线 Demo / 托管平台用的依赖清单。
#
# ⚠️ 这份文件是从 pyproject.toml 的 dependencies 同步来的，**不要手改**：
#    改了之后 `tests/test_demo_deploy.py` 会因为两边不一致而失败。
#    要升级依赖请改 pyproject.toml，然后重跑：
#        .\\.venv\\Scripts\\python.exe scripts\\sync_requirements.py
#
# 为什么还需要它：Streamlit Community Cloud 等托管平台默认只认 requirements.txt，
# 不会去读 pyproject.toml 的依赖表。
"""

_BLOCK = re.compile(r"^dependencies\s*=\s*\[(.*?)\]", re.DOTALL | re.MULTILINE)
_QUOTED = re.compile(r'"([^"]+)"')


def pyproject_dependencies() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = _BLOCK.search(text)
    if not match:
        raise SystemExit("[失败] pyproject.toml 里找不到 dependencies 列表")
    return _QUOTED.findall(match.group(1))


def requirements_dependencies() -> list[str]:
    if not REQUIREMENTS.is_file():
        return []
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    return [
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def render(dependencies: list[str]) -> str:
    return HEADER + "\n" + "\n".join(dependencies) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 requirements.txt")
    parser.add_argument("--check", action="store_true", help="只检查是否一致，不写入")
    args = parser.parse_args()

    expected = pyproject_dependencies()
    actual = requirements_dependencies()
    if args.check:
        if expected == actual:
            print(f"[通过] requirements.txt 与 pyproject.toml 一致（{len(expected)} 个依赖）")
            return 0
        print("[失败] 两边不一致：")
        print(f"  pyproject.toml: {expected}")
        print(f"  requirements.txt: {actual}")
        return 1

    REQUIREMENTS.write_text(render(expected), encoding="utf-8")
    print(f"[完成] requirements.txt 已同步（{len(expected)} 个依赖）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
