"""Runtime configuration loaded from environment variables."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with no embedded credentials or account IDs."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    runtime_mode: Literal["local", "aws"] = "local"
    synthetic_demo: bool = False
    app_env: str = "local"
    app_log_level: str = "INFO"
    aws_region: str = ""
    bedrock_model_id: str = ""
    input_bucket: str = ""
    result_bucket: str = ""
    cases_table: str = ""
    min_extraction_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
