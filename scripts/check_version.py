"""检查版本号是否一致：`pyproject.toml` ↔ `src/advisor_fit/__init__.py`。

版本号写两处容易忘记同步，所以用脚本卡住（CI 里也会跑）。
退出码 0 = 一致，1 = 不一致或读不到。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "src" / "advisor_fit" / "__init__.py"

_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_DUNDER_LINE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def read_versions() -> tuple[str, str]:
    pyproject = _VERSION_LINE.search(PYPROJECT.read_text(encoding="utf-8"))
    init = _DUNDER_LINE.search(INIT.read_text(encoding="utf-8"))
    return (
        pyproject.group(1) if pyproject else "",
        init.group(1) if init else "",
    )


def main() -> int:
    pyproject, dunder = read_versions()
    if not pyproject or not dunder:
        print(f"[失败] 读不到版本号：pyproject={pyproject!r} __init__={dunder!r}")
        return 1
    if pyproject != dunder:
        print(f"[失败] 版本号不一致：pyproject.toml={pyproject}，__init__.py={dunder}")
        return 1
    print(f"[通过] 版本号一致：{pyproject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
