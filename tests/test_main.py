import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from src.collectors.http_client import CollectionError, HttpClient


def test_main_saves_fixture_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = Path(__file__).parent / "fixtures" / "vivareal_exemplo.html"
    monkeypatch.setenv("PROPERTY_SOURCE", "vivareal")
    monkeypatch.setenv("SOURCE_URL", "https://example.com/busca")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(HttpClient, "fetch_page", Mock(return_value=fixture.read_text(encoding="utf-8")))
    assert main.main() == 0
    path = tmp_path / "properties.json"
    assert path.is_file()
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 2


def test_main_fails_without_writing_if_access_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PROPERTY_SOURCE", "vivareal")
    monkeypatch.setenv("SOURCE_URL", "https://example.com/busca")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(HttpClient, "fetch_page", Mock(side_effect=CollectionError("HTTP 403")))
    assert main.main() == 1
    assert "HTTP 403" in caplog.text
    assert not list(tmp_path.iterdir())
