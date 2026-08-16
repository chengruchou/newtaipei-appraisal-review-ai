from appraisal_review.api.app import app, health


def test_health() -> None:
    assert health() == {"status": "ok"}


def test_validation_route_is_in_openapi_contract() -> None:
    assert "/v1/validate" in app.openapi()["paths"]
