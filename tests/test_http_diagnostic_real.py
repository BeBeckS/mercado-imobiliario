import os
from urllib.parse import urlsplit

import pytest
import requests

from src.collectors.http_client import HttpClient
from src.config.settings import Settings


@pytest.mark.skipif(
    os.getenv("RUN_HTTP_DIAGNOSTIC") != "1",
    reason="Diagnóstico HTTP real exige execução explícita",
)
def test_configured_http_access() -> None:
    settings = Settings.from_env()
    parsed_url = urlsplit(settings.source_url)
    print(f"Domínio: {parsed_url.hostname}")

    client = HttpClient(settings)
    statuses: list[int] = []

    def observe_response(response: requests.Response, *args: object, **kwargs: object) -> None:
        statuses.append(response.status_code)

    client.session.hooks["response"].append(observe_response)
    html: str | None = None
    try:
        html = client.fetch_page()
    finally:
        client.close()
        robots_status = str(statuses[0]) if statuses else "não recebido"
        page_status = str(statuses[1]) if len(statuses) > 1 else "não requisitada ou sem resposta"
        size = str(len(html)) if html is not None else "indisponível"
        print(f"robots.txt HTTP: {robots_status}; página HTTP: {page_status}; caracteres HTML: {size}")
    assert html.strip()
