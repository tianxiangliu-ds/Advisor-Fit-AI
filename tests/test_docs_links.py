"""文档自检：README 里引用的本地图片与链接必须真的存在。

为什么值得一条测试：GitHub 首页图裂是最掉价的一种低级错误，
而截图文件改名/重生成时特别容易漏改 README。机械地卡住它。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|<img[^>]+src=\"([^\"]+)\"")
_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)#]+)\)")


def _local_targets(
    pattern: re.Pattern[str], text: str, *, skip_prefixes: tuple[str, ...]
) -> list[str]:
    targets: list[str] = []
    for match in pattern.finditer(text):
        target = next((group for group in match.groups() if group), "")
        if not target or target.startswith(skip_prefixes):
            continue
        targets.append(target)
    return targets


def test_readme_local_images_exist():
    text = README.read_text(encoding="utf-8")
    images = _local_targets(_IMAGE, text, skip_prefixes=("http://", "https://", "data:"))

    assert images, "README 里应该至少有一张界面截图"
    missing = [target for target in images if not (ROOT / target).is_file()]
    assert missing == [], f"README 引用的图片不存在：{missing}"


def test_readme_local_links_exist():
    text = README.read_text(encoding="utf-8")
    links = _local_targets(
        _LINK, text, skip_prefixes=("http://", "https://", "#", "mailto:")
    )

    missing = [target for target in links if not (ROOT / target.split("#")[0]).exists()]
    assert missing == [], f"README 引用的本地路径不存在：{missing}"


def test_readme_screenshots_are_not_duplicated():
    """同一张图在 README 里出现两次，多半是两段文字讲了同一件事。"""
    text = README.read_text(encoding="utf-8")
    images = _local_targets(_IMAGE, text, skip_prefixes=("http://", "https://", "data:"))

    seen: dict[str, int] = {}
    for target in images:
        seen[target] = seen.get(target, 0) + 1
    duplicated = {target: count for target, count in seen.items() if count > 1}
    assert duplicated == {}, f"README 里重复引用了同一张截图：{duplicated}"


def test_every_screenshot_is_referenced():
    """反过来也要成立：仓库里躺着的截图应该都被 README 用到，别留孤儿文件。"""
    text = README.read_text(encoding="utf-8")
    referenced = {
        Path(target).name
        for target in _local_targets(_IMAGE, text, skip_prefixes=("http://", "https://", "data:"))
    }
    on_disk = {path.name for path in (ROOT / "screenshots").glob("*.png")}

    orphans = sorted(on_disk - referenced)
    assert orphans == [], f"这些截图没有被 README 引用：{orphans}"
