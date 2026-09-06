from pathlib import Path

import pytest
from pydantic import ValidationError

from appraisal_review.adapters.local.pdf_config import (
    PDFRenderConfig,
    PDFTemplatePolicy,
    render_config_from_settings,
)
from appraisal_review.config import Settings
from appraisal_review.domain.pdf_models import PDFFieldPlacementError


def render_config(font_path: Path, **changes: object) -> PDFRenderConfig:
    values: dict[str, object] = {"font_path": font_path}
    values.update(changes)
    return PDFRenderConfig.model_validate(values)


def test_render_config_is_explicit_and_performs_no_font_io(tmp_path: Path) -> None:
    missing_absolute_font = tmp_path / "not-installed.ttf"

    config = render_config(missing_absolute_font)

    assert config.font_path == missing_absolute_font
    assert not missing_absolute_font.exists()
    assert config.text_alignment == "center"
    assert config.overwrite_existing is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"font_path": Path("relative.ttf")}, "font path must be absolute"),
        ({"font_name": "bad name"}, "string_pattern_mismatch"),
        ({"font_size": 0.0}, "greater_than"),
        ({"text_color": "black"}, "string_pattern_mismatch"),
        ({"text_alignment": "justify"}, "literal_error"),
        ({"annotation_label": "  "}, "string_too_short"),
        ({"annotation_color": "#12345G"}, "string_pattern_mismatch"),
        ({"decimal_places": 7}, "less_than_equal"),
        ({"show_positive_sign": 1}, "bool_type"),
        ({"overwrite_existing": 0}, "bool_type"),
    ],
)
def test_invalid_render_policy_fails(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {"font_path": tmp_path / "font.ttf"}
    values.update(change)
    with pytest.raises(ValidationError, match=message):
        PDFRenderConfig.model_validate(values)


def test_color_is_normalized_without_relaxing_validation(tmp_path: Path) -> None:
    config = render_config(tmp_path / "font.ttf", text_color="#abcdef", annotation_color="#0a1b2c")

    assert config.text_color == "#ABCDEF"
    assert config.annotation_color == "#0A1B2C"


def test_template_policy_separates_editable_and_reference_pages() -> None:
    policy = PDFTemplatePolicy(
        template_id="synthetic-v1",
        editable_pages=frozenset({1, 2, 3}),
        reference_only_pages=frozenset({4, 5, 6}),
    )

    policy.require_editable_page(3)
    with pytest.raises(PDFFieldPlacementError, match="not explicitly editable"):
        policy.require_editable_page(4)


@pytest.mark.parametrize(
    "values",
    [
        {
            "template_id": "synthetic-v1",
            "editable_pages": frozenset(),
            "reference_only_pages": frozenset({1}),
        },
        {
            "template_id": "synthetic-v1",
            "editable_pages": frozenset({1, 2}),
            "reference_only_pages": frozenset({2, 3}),
        },
        {
            "template_id": "synthetic-v1",
            "editable_pages": frozenset({0, 1}),
            "reference_only_pages": frozenset(),
        },
    ],
)
def test_invalid_template_page_policy_fails(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PDFTemplatePolicy.model_validate(values)


def test_application_settings_expose_non_secret_pdf_policy() -> None:
    settings = Settings(
        _env_file=None,
        pdf_font_path="C:/configured/font.ttf",
        pdf_font_name="ConfiguredFont",
        pdf_font_size=9.5,
        pdf_text_color="#112233",
        pdf_text_alignment="right",
        pdf_annotation_label="CHECK",
        pdf_annotation_color="#AA0000",
        pdf_decimal_places=1,
        pdf_show_positive_sign=False,
        pdf_overwrite_existing=True,
    )

    assert settings.pdf_font_path == "C:/configured/font.ttf"
    assert settings.pdf_font_name == "ConfiguredFont"
    assert settings.pdf_font_size == 9.5
    assert settings.pdf_text_alignment == "right"
    assert settings.pdf_decimal_places == 1
    assert settings.pdf_show_positive_sign is False
    assert settings.pdf_overwrite_existing is True


def test_unrelated_settings_do_not_require_a_pdf_font() -> None:
    settings = Settings(_env_file=None)

    assert settings.pdf_font_path == ""


def test_example_environment_preserves_hex_color_values() -> None:
    example = Path(__file__).parents[2] / ".env.example"

    settings = Settings(_env_file=example)

    assert settings.pdf_text_color == "#000000"
    assert settings.pdf_annotation_color == "#B00020"


def test_real_writer_config_is_built_explicitly_from_settings(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        pdf_font_path=str(tmp_path / "configured.ttf"),
        pdf_font_size=11.0,
        pdf_text_alignment="left",
    )

    config = render_config_from_settings(settings)

    assert config.font_path == tmp_path / "configured.ttf"
    assert config.font_size == 11.0
    assert config.text_alignment == "left"


def test_empty_default_font_path_fails_only_when_real_writer_is_composed() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValidationError, match="font path must be absolute"):
        render_config_from_settings(settings)
