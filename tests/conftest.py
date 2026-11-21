import pytest


@pytest.fixture(autouse=True)
def isolate_collection_configuration(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    if request.node.path.name == "test_http_diagnostic_real.py":
        return
    for name in (
        "PROPERTY_SOURCE", "LOCAL_HTML_PATH", "SOURCE_URL", "USER_AGENT",
        "REQUEST_TIMEOUT_SECONDS", "REQUEST_INTERVAL_SECONDS", "OUTPUT_DIR", "LOG_LEVEL",
        "PYTHON_DOTENV_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
