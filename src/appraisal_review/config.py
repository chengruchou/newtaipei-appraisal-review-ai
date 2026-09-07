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
    pdf_font_path: str = ""
    pdf_font_name: str = "AppraisalCJK"
    pdf_font_size: float = Field(default=10.0, gt=0.0, le=72.0)
    pdf_text_color: str = "#000000"
    pdf_text_alignment: Literal["left", "center", "right"] = "center"
    pdf_annotation_label: str = "REVIEW"
    pdf_annotation_color: str = "#B00020"
    pdf_decimal_places: int = Field(default=2, ge=0, le=6)
    pdf_show_positive_sign: bool = True
    pdf_overwrite_existing: bool = False
    pdf_grade_labels: dict[str, str] = Field(
        default_factory=lambda: {
            "excellent": "excellent",
            "slightly_superior": "slightly_superior",
            "normal": "normal",
            "slightly_inferior": "slightly_inferior",
            "inferior": "inferior",
        }
    )
