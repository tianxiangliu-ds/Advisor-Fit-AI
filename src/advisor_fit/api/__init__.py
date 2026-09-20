"""HTTP 接口层。

`create_app()` 是唯一入口：

```python
from advisor_fit.api import create_app
app = create_app()
```

命令行启动：

```powershell
.\\.venv\\Scripts\\python.exe -m uvicorn "advisor_fit.api:create_app" --factory --port 8000
```

注意：业务编排在 `advisor_fit.services`，本包只负责"把 HTTP 请求翻译成一次服务调用"。
所以**导入 api 会拉起 FastAPI，导入 services 不会**——服务层能脱离 Web 框架单测。
"""

from advisor_fit.api.app import create_app

__all__ = ["create_app"]
