"""环境变量与本地路径配置。API Key 只从 .env / 环境读取，绝不进入前端或日志。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：src/advisor_fit/config.py -> 上溯三层。
#
# 为什么要把路径固定成绝对路径，而不是用 Path("data")：
# 相对路径是**跟着启动目录走**的。爬取工具单独放在另一个文件夹后，如果从那边启动，
# `data/advisors.db` 就会落到爬取文件夹里，而 App 从产品目录启动时读的是产品目录下的
# 那一份——两边悄悄读不同的库，是最难查的一类 bug。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# 装成 wheel 后 __file__ 在 site-packages 里，上溯不到项目根；这时退回按启动目录找。
_ROOT = _PROJECT_ROOT if (_PROJECT_ROOT / "pyproject.toml").exists() else Path.cwd()


class Settings(BaseSettings):
    # env_file 也必须是绝对路径：否则从爬取文件夹启动时会读不到产品目录下的 .env，
    # 大模型 Key 就"凭空消失"了。
    model_config = SettingsConfigDict(
        env_file=str(_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    llm_provider: str = "deepseek"  # deepseek | openai | anthropic | ollama
    llm_api_key: str = ""
    llm_base_url: str = ""  # 留空则用 provider 默认地址
    llm_model: str = ""      # 留空则用 provider 默认模型
    llm_store: bool = False

    # 可选：中文库（万方）。不配也能检索，默认主干是免费免 Key 的国际学术库。
    wanfang_app_key: str = ""
    # 可选：AMiner 开放平台 Token（按次计费，默认在检索源表里关闭）
    aminer_api_key: str = ""
    # 可选：进入 OpenAlex / Crossref 的「礼貌池」用的联系邮箱，留空也能用
    contact_email: str = ""

    # 可选：语义召回用的向量接口（OpenAI 兼容 /embeddings）。
    # 留空则用内置的离线档——它只做**表层相似**，不是同义词理解，详见 providers/embedding.py
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""
    # 可选：本机模型名（需自行安装 sentence-transformers，见 pyproject 的 embed 分组）
    embedding_local_model: str = ""

    data_dir: Path = _ROOT / "data"
    uploads_dir: Path = _ROOT / "uploads"
    exports_dir: Path = _ROOT / "exports"


settings = Settings()
