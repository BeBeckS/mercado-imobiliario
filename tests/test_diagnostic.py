import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.collectors.diagnostic import diagnose_html, main
from src.collectors.http_client import CollectionError, HttpClient
from src.config.settings import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "diagnostico_exemplo.html"


def test_html_and_embedded_data_are_summarized_without_values() -> None:
    report = diagnose_html(FIXTURE.read_text(encoding="utf-8"))
    assert report["elementos_candidatos"] == 2
    assert report["exemplos_candidatos"][0]["seletor"] == "article.property-card"
    assert {"titulo", "preco", "localizacao", "area", "quartos", "banheiros", "vagas", "url"} <= set(
        report["exemplos_candidatos"][0]["sinais"]
    )
    assert "span.listing-price" in report["seletores_por_sinal"]["preco"]
    assert report["atributos_data_relevantes"] == {"data-listing-id": 1}
    assert report["json_ld_scripts"] == 1
    assert report["json_embutido_blocos"] == 2
    assert report["tipos_json_ld"] == ["Apartment"]
    assert report["tipos_json_embutido"] == ["SearchResults"]
    assert {"titulo", "preco", "url", "identificador", "quartos", "area"} <= set(report["sinais_json"])
    assert report["objetos_json_candidatos"] >= 1
    output = json.dumps(report)
    assert "secret-fixture" not in output
    assert "450.000" not in output


def test_only_scripts_does_not_claim_javascript_dependency() -> None:
    report = diagnose_html('<html><div id="app"></div><script src="bundle.js"></script></html>')
    assert report["elementos_candidatos"] == 0
    assert report["scripts_total"] == 1
    assert "Não é conclusivo" in report["interpretacao"]


def test_possible_access_restriction_is_reported_without_page_content() -> None:
    report = diagnose_html("<html><body>Access denied: CAPTCHA</body></html>")
    assert report["possiveis_restricoes"] == ["captcha", "acesso negado"]
    assert report["elementos_candidatos"] == 0


def test_invalid_structured_json_is_reported_without_body() -> None:
    report = diagnose_html('<script type="application/ld+json">{secret-invalid}</script>')
    assert report["json_ld_scripts"] == 1
    assert report["json_invalido"] == 1
    assert report["tipos_json_ld"] == []


def test_local_fixture_does_not_call_settings_or_http(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Mock(side_effect=AssertionError("não deve carregar configuração"))
    monkeypatch.setattr(Settings, "from_env", settings)
    assert main(["--html-file", str(FIXTURE)]) == 0
    assert json.loads(capsys.readouterr().out)["elementos_candidatos"] == 2
    settings.assert_not_called()


def test_remote_uses_existing_client_and_closes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings("https://example.com", "AcademicBot/1.0", 5, 0, Path("."), "INFO")
    fetch = Mock(return_value=FIXTURE.read_text(encoding="utf-8"))
    close = Mock()
    monkeypatch.setattr(Settings, "from_env", Mock(return_value=settings))
    monkeypatch.setattr(HttpClient, "fetch_page", fetch)
    monkeypatch.setattr(HttpClient, "close", close)
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["elementos_candidatos"] == 2
    fetch.assert_called_once()
    close.assert_called_once()


def test_failure_redacts_url_and_closes(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings("https://example.com?token=secret-fixture", "AcademicBot/1.0", 5, 0, Path("."), "INFO")
    close = Mock()
    monkeypatch.setattr(Settings, "from_env", Mock(return_value=settings))
    monkeypatch.setattr(
        HttpClient, "fetch_page",
        Mock(side_effect=CollectionError("Acesso a https://example.com?token=secret-fixture: HTTP 403")),
    )
    monkeypatch.setattr(HttpClient, "close", close)
    with caplog.at_level(logging.ERROR):
        assert main([]) == 1
    assert "HTTP 403" in caplog.text
    assert "secret-fixture" not in caplog.text
    close.assert_called_once()


def test_empty_local_file_fails(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "vazio.html"
    path.write_text("   ", encoding="utf-8")
    assert main(["--html-file", str(path)]) == 1
    assert "HTML vazio" in caplog.text
