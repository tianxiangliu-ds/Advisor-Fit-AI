"""仓库级 pytest 环境适配（DSH Windows 沙箱专用）。

问题：在受限沙箱下，用 `mkdir(mode=0o700)` 建出来的目录**连自己都读不出来**
（`os.scandir` 直接 PermissionError）。而 pytest 建立临时目录时一律使用 0o700：

1. `pytest-of-<用户名>`（临时目录根下的用户目录）
2. `pytest-N`（每次运行的基准目录）
3. `test_xxx0`（每个用例的临时目录）

所以默认情况下，依赖 `tmp_path` 的用例会在 setup 阶段批量报错。

解决：
- 把 pytest 的临时目录根指到项目内的 `.tmp/`（不放系统 TEMP/TMP），**且每次运行用全新子目录**，
  这样 pytest 永远不需要删除或扫描上一次留下的目录；
- 把 pytest 建临时目录时的权限位从 0o700 放宽为 0o777；
- 预先按默认权限建好 `pytest-of-<用户名>`，让 pytest 的 `mkdir(..., exist_ok=True)` 变成空操作。

这样在受限沙箱下也能完整跑测试，不需要申请更宽的文件权限。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_RUN_TMP_ROOT = _REPO_ROOT / ".tmp" / f"run-{os.getpid()}-{int(time.time())}"
_RUN_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PYTEST_DEBUG_TEMPROOT"] = str(_RUN_TMP_ROOT)


def _relax_pytest_temp_permissions() -> None:
    try:
        import _pytest.pathlib as pytest_pathlib
        import _pytest.tmpdir as pytest_tmpdir
    except Exception:  # pragma: no cover - pytest 内部结构变化时不影响测试
        return

    def _numbered_dir(root, prefix, mode=0o777):
        return pytest_pathlib.make_numbered_dir(root=root, prefix=prefix, mode=0o777)

    def _numbered_dir_with_cleanup(*, prefix, root, keep, lock_timeout, mode, register=None):
        return pytest_pathlib.make_numbered_dir_with_cleanup(
            prefix=prefix,
            root=root,
            keep=keep,
            lock_timeout=lock_timeout,
            mode=0o777,
            register=register,
        )

    pytest_tmpdir.make_numbered_dir = _numbered_dir
    pytest_tmpdir.make_numbered_dir_with_cleanup = _numbered_dir_with_cleanup

    names = {os.environ.get("USERNAME") or "", "unknown"}
    get_user = getattr(pytest_tmpdir, "get_user", None)
    if callable(get_user):
        names.add(get_user() or "unknown")
    for name in names:
        if name:
            (_RUN_TMP_ROOT / f"pytest-of-{name}").mkdir(parents=True, exist_ok=True)


_relax_pytest_temp_permissions()
