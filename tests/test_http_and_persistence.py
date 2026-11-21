import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from src.collectors.http_client import CollectionError, HttpClient
from src.collectors.parser import parse_listings
from src.config.settings import Settings
from src.utils.persistence import save_listings


def settings(tmp_path: Path) -> Settings:
    return Settings("https://example.com/busca", "AcademicBot/1.0", 3, 0, tmp_path, "INFO")


def response(status: int, text: str, content_type: str = "text/plain") -> Mock:
    return Mock(status_code=status, text=text, headers={"Content-Type": content_type})


def test_client_observes_robots_and_request_configuration(tmp_path: Path) -> None:
    client = HttpClient(settings(tmp_path))
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nAllow: /"),
        response(200, "<html>ok</html>", "text/html"),
    ])
    assert client.fetch_page() == "<html>ok</html>"
    assert client.session.headers["User-Agent"] == "AcademicBot/1.0"
    assert client.session.get.call_count == 2
    client.session.get.assert_any_call("https://example.com/robots.txt", timeout=3, allow_redirects=False)
    client.session.get.assert_any_call("https://example.com/busca", timeout=3, allow_redirects=False)
    client.close()


@pytest.mark.parametrize("robots", [
    response(403, ""),
    response(200, "User-agent: *\nDisallow: /"),
    response(200, "User-agent: *\nCrawl-delay: 5"),
    response(200, "User-agent: *\nRequest-rate: 1/10"),
])
def test_unavailable_or_disallowed_robots_stops_before_page(tmp_path: Path, robots: Mock) -> None:
    client = HttpClient(settings(tmp_path))
    client.session.get = Mock(return_value=robots)
    with pytest.raises(CollectionError):
        client.fetch_page()
    assert client.session.get.call_count == 1
    client.close()


def test_network_failure_is_explicit(tmp_path: Path) -> None:
    client = HttpClient(settings(tmp_path))
    client.session.get = Mock(side_effect=requests.Timeout("tempo esgotado"))
    with pytest.raises(CollectionError, match="Timeout"):
        client.fetch_page()
    client.close()


def test_interval_applies_between_robots_and_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient(replace(settings(tmp_path), interval_seconds=2))
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nAllow: /"),
        response(200, "<html>ok</html>", "text/html"),
    ])
    monotonic = Mock(side_effect=[10.0, 10.5, 12.0])
    sleep = Mock()
    monkeypatch.setattr("src.collectors.http_client.time.monotonic", monotonic)
    monkeypatch.setattr("src.collectors.http_client.time.sleep", sleep)
    client.fetch_page()
    sleep.assert_called_once_with(1.5)
    client.close()


def test_denied_page_does_not_return_html(tmp_path: Path) -> None:
    client = HttpClient(settings(tmp_path))
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nAllow: /"),
        response(403, ""),
    ])
    with pytest.raises(CollectionError, match="HTTP 403"):
        client.fetch_page()
    client.close()


def test_persistence_creates_distinct_valid_json_files(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fixture = Path(__file__).parent / "fixtures" / "vivareal_exemplo.html"
    listings = parse_listings(fixture.read_text(encoding="utf-8"), "https://example.com", now)
    first = save_listings(listings, tmp_path, now)
    second = save_listings(listings, tmp_path, now)
    assert first != second
    data = json.loads(first.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["preco"] == "450000"
    assert data[0]["data_coleta"] == "2026-01-01T00:00:00Z"
    assert data[1]["bairro"] is None
