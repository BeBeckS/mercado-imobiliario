from dataclasses import replace
from decimal import Decimal
import logging
from pathlib import Path
import traceback
from typing import Iterator
from unittest.mock import Mock

import pytest
import requests

from src.collectors.http_client import CollectionError, HttpClient
from src.config.settings import Settings


@pytest.fixture
def client(tmp_path: Path) -> Iterator[HttpClient]:
    settings = Settings(
        source_url="https://example.com/imoveis",
        user_agent="AcademicBot/1.0 (contato: contato@example.com)",
        timeout_seconds=5,
        interval_seconds=0,
        output_dir=tmp_path,
        log_level="INFO",
    )
    instance = HttpClient(settings)
    yield instance
    instance.close()


def response(status: int, text: str, content_type: str = "text/plain") -> Mock:
    return Mock(status_code=status, text=text, headers={"Content-Type": content_type})


def test_fetch_page_returns_html_and_uses_configured_headers_and_timeout(client: HttpClient) -> None:
    html = "<html><body>Página</body></html>"
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nAllow: /"),
        response(200, html, "text/html; charset=utf-8"),
    ])
    assert client.fetch_page() == html
    assert client.session.headers["User-Agent"] == client.settings.user_agent
    client.session.get.assert_called_with(
        client.settings.source_url, timeout=5, allow_redirects=False
    )


def test_http_logs_keep_host_path_and_status_without_query_or_fragment(
    client: HttpClient, caplog: pytest.LogCaptureFixture
) -> None:
    url = "https://example.com/imoveis?token=segredo-teste#fragmento-secreto"
    client.settings = replace(client.settings, source_url=url)
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nAllow: /"),
        response(200, "<html>ok</html>", "text/html"),
    ])
    with caplog.at_level(logging.INFO, logger="src.collectors.http_client"):
        assert client.fetch_page() == "<html>ok</html>"
    assert "GET https://example.com/imoveis HTTP 200" in caplog.text
    assert "segredo-teste" not in caplog.text and "fragmento-secreto" not in caplog.text
    assert client.session.get.call_args_list[-1].args[0] == url


def test_http_library_debug_and_retry_logs_do_not_expose_queries(
    client: HttpClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.settings = replace(client.settings, source_url="https://example.com/imoveis?token=segredo-teste")
    responses = iter([
        response(200, "User-agent: *\nAllow: /"),
        response(200, "<html>ok</html>", "text/html"),
    ])

    def simulated_get(url: str, **kwargs: object) -> Mock:
        logging.getLogger("urllib3.connectionpool").debug("GET %s", url)
        logging.getLogger("urllib3.connectionpool").warning("Retrying %s", url)
        return next(responses)

    client.session.get = Mock(side_effect=simulated_get)
    with caplog.at_level(logging.DEBUG):
        assert client.fetch_page() == "<html>ok</html>"
    assert "GET https://example.com/imoveis HTTP 200" in caplog.text
    assert "segredo-teste" not in caplog.text


@pytest.mark.parametrize("failure", [
    response(403, ""),
    requests.Timeout("https://example.com/imoveis?token=segredo-teste"),
    requests.ConnectionError("https://example.com/imoveis?token=segredo-teste"),
    requests.exceptions.InvalidURL("https://example.com/imoveis?token=segredo-teste"),
])
def test_http_errors_do_not_expose_query_or_exception_message(
    client: HttpClient, caplog: pytest.LogCaptureFixture, failure: object
) -> None:
    client.settings = replace(client.settings, source_url="https://example.com/imoveis?token=segredo-teste")
    client.session.get = Mock(side_effect=[response(200, "User-agent: *\nAllow: /"), failure])
    with caplog.at_level(logging.INFO, logger="src.collectors.http_client"):
        with pytest.raises(CollectionError) as raised:
            client.fetch_page()
    rendered = "".join(traceback.format_exception(raised.type, raised.value, raised.tb))
    assert "segredo-teste" not in rendered and "segredo-teste" not in caplog.text
    assert "GET https://example.com/imoveis" in str(raised.value)


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (response(403, ""), "HTTP 403"),
        (requests.Timeout("tempo esgotado"), "Timeout"),
        (requests.ConnectionError("servidor indisponível"), "Erro de conexão"),
        (requests.exceptions.InvalidURL("URL malformada"), "URL inválida"),
        (response(200, "", "text/html"), "conteúdo HTML vazio"),
        (response(200, " \n\t", "text/html"), "conteúdo HTML vazio"),
    ],
)
def test_fetch_page_reports_errors(client: HttpClient, failure: object, message: str) -> None:
    client.session.get = Mock(side_effect=[response(200, "User-agent: *\nAllow: /"), failure])
    with pytest.raises(CollectionError, match=message):
        client.fetch_page()


@pytest.mark.parametrize("url", ["not-a-url", "https://[invalid", "https://example.com:abc", "https://exa mple.com"])
def test_invalid_source_url_is_rejected_before_request(client: HttpClient, url: str) -> None:
    client.settings = replace(client.settings, source_url=url)
    client.session.get = Mock()
    with pytest.raises(CollectionError, match="URL da fonte inválida"):
        client.fetch_page()
    client.session.get.assert_not_called()


def test_fetch_json_checks_robots_once_and_preserves_decimal(client: HttpClient) -> None:
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nAllow: /"),
        response(200, '{"area": 85.75}', "application/json; charset=utf-8"),
        response(200, '{"area": 1}', "application/json"),
    ])
    assert client.fetch_json("https://example.com/api?p=1") == {"area": Decimal("85.75")}
    assert client.fetch_json("https://example.com/api?p=2") == {"area": 1}
    assert client.session.get.call_count == 3
    client.session.get.assert_called_with(
        "https://example.com/api?p=2", timeout=5, allow_redirects=False
    )


def test_fetch_json_checks_robots_for_each_target(client: HttpClient) -> None:
    client.session.get = Mock(side_effect=[
        response(200, "User-agent: *\nDisallow: /api/private"),
        response(200, "{}", "application/json"),
    ])
    client.fetch_json("https://example.com/api/public")
    with pytest.raises(CollectionError, match="proibida pelo robots"):
        client.fetch_json("https://example.com/api/private")
    assert client.session.get.call_count == 2


@pytest.mark.parametrize("robots", [
    "\ufeff# comentário\nUser-agent: *\nDisallow: /privado",
    "Sitemap: https://example.com/sitemap.xml",
])
def test_valid_robots_content_allows_access(client: HttpClient, robots: str) -> None:
    client.session.get = Mock(side_effect=[
        response(200, robots, "text/html"),
        response(200, "{}", "application/json"),
    ])
    assert client.fetch_json("https://example.com/api") == {}
    assert client.session.get.call_count == 2


@pytest.mark.parametrize("robots", [
    "<!DOCTYPE html><html><body>CAPTCHA</body></html>",
    "<title>Acesso negado</title>",
    '{"error": "access denied"}',
    "Access denied",
    "",
    "# somente comentários",
    "User-agent:",
    "User-agent: *\nAccess denied",
])
@pytest.mark.parametrize("method", ["page", "json"])
def test_unverifiable_robots_blocks_target_request(
    client: HttpClient, robots: str, method: str
) -> None:
    client.session.get = Mock(return_value=response(200, robots))
    with pytest.raises(CollectionError, match="robots.txt"):
        if method == "page":
            client.fetch_page()
        else:
            client.fetch_json("https://example.com/api")
    client.session.get.assert_called_once_with(
        "https://example.com/robots.txt", timeout=5, allow_redirects=False
    )


def test_invalid_robots_is_not_cached(client: HttpClient) -> None:
    client.session.get = Mock(side_effect=[
        response(200, "<html>bloqueio</html>"),
        response(200, "User-agent: *\nAllow: /"),
        response(200, "{}", "application/json"),
    ])
    with pytest.raises(CollectionError, match="robots.txt"):
        client.fetch_json("https://example.com/api")
    assert client.fetch_json("https://example.com/api") == {}
    assert client.session.get.call_count == 3


@pytest.mark.parametrize("robots", [
    response(403, ""),
    response(200, "User-agent: *\nDisallow: /api"),
    response(200, "User-agent: *\nCrawl-delay: 5"),
    response(200, "User-agent: *\nRequest-rate: 1/10"),
])
def test_fetch_json_stops_when_robots_is_unavailable_or_restrictive(
    client: HttpClient, robots: Mock
) -> None:
    client.session.get = Mock(return_value=robots)
    with pytest.raises(CollectionError):
        client.fetch_json("https://example.com/api")
    client.session.get.assert_called_once_with(
        "https://example.com/robots.txt", timeout=5, allow_redirects=False
    )


@pytest.mark.parametrize(("failure", "message"), [
    (response(403, ""), "HTTP 403"),
    (response(302, ""), "HTTP 302"),
    (requests.Timeout("timeout"), "Timeout"),
    (response(200, "{}", "text/html"), "não é JSON"),
    (response(200, "{", "application/json"), "JSON inválida"),
    (response(200, '{"n": NaN}', "application/json"), "JSON inválida"),
])
def test_fetch_json_reports_http_and_content_errors(
    client: HttpClient, failure: object, message: str
) -> None:
    client.session.get = Mock(side_effect=[response(200, "User-agent: *\nAllow: /"), failure])
    with pytest.raises(CollectionError, match=message):
        client.fetch_json("https://example.com/api")


def test_fetch_json_does_not_access_another_origin(client: HttpClient) -> None:
    client.session.get = Mock()
    with pytest.raises(CollectionError, match="origem configurada"):
        client.fetch_json("https://other.example/api")
    client.session.get.assert_not_called()
