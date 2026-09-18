"""环境变量与本地路径配置。API Key 只从 .env / 环境读取，绝不进入前端或日志。"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_store: bool = False

    openalex_mailto: str = ""
    advisor_fit_user_agent: str = "AdvisorFitBot/0.1 (contact: you@example.com)"

    data_dir: Path = Path("data")
    uploads_dir: Path = Path("uploads")
    exports_dir: Path = Path("exports")


settings = Settings()
