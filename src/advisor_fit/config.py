"""环境变量与本地路径配置。API Key 只从 .env / 环境读取，绝不进入前端或日志。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "deepseek"  # deepseek | openai | anthropic | ollama
    llm_api_key: str = ""
    llm_base_url: str = ""  # 留空则用 provider 默认地址
    llm_model: str = ""      # 留空则用 provider 默认模型
    llm_store: bool = False

    wanfang_app_key: str = ""

    data_dir: Path = Path("data")
    uploads_dir: Path = Path("uploads")
    exports_dir: Path = Path("exports")


settings = Settings()
