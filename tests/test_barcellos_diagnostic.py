import json
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.barcellos_diagnostic import diagnose_barcellos, main
from src.collectors.http_client import CollectionError, HttpClient
from src.config.settings import Settings


HTML = """
<html><head>
<meta property="og:title" content="valor privado">
<script type="application/ld+json">
{"@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem"}]}
</script>
<script>window.SAMI_SITE = {"apiImoveis":"secret-fixture","perPage":16};</script>
<script src="/js/busca.js"></script>
<script src="/js/scroll-infinito.js"></script>
</head><body>
<div id="lista-imoveis" class="grid-imoveis"></div>
<div id="scroll-sentinela" class="scroll-sentinela"></div>
<div id="scroll-loading" class="scroll-loading" hidden></div>
</body></html>
"""


def test_empty_grid_reports_unknown_fields_without_leaking_values() -> None:
    report = diagnose_barcellos(HTML)
    assert report["container"]["estado"] == "vazio"
    assert report["cards"]["confirmados"] == 0
    assert report["cards"]["seletor_individual"] is None
    fields = report["campos_dos_cards"]
    assert fields["cards_avaliados"] == 0
    assert fields["ausentes"] is fields["presentes_vazios"] is fields["nao_convertiveis"] is None
    structured = report["dados_estruturados"]
    assert structured["tipos_json_ld"] == ["BreadcrumbList", "ListItem"]
    assert structured["configuracao_site"] == {
        "estado": "encontrado", "chaves": ["apiImoveis", "perPage"], "por_pagina": 16
    }
    assert structured["metadados_pagina"] == ["og:title"]
    assert all(report["carregamento"].values())
    output = json.dumps(report)
    assert "secret-fixture" not in output
    assert "valor privado" not in output


def test_unknown_structure_is_not_counted_as_confirmed_cards() -> None:
    missing = diagnose_barcellos("<html><body></body></html>")
    assert missing["container"]["estado"] == "ausente"
    assert missing["cards"]["confirmados"] is None
    populated = diagnose_barcellos(
        '<div id="lista-imoveis" class="grid-imoveis" data-kind="x">'
        '<div><a href="/relativo">a</a><a href="https://example.com/imovel">b</a>'
        '<a href="#fragmento">c</a><a href="">d</a></div></div>'
    )
    assert populated["container"]["estado"] == "nao_vazio"
    assert populated["container"]["elementos_filhos_diretos"] == 1
    assert populated["cards"]["confirmados"] is None
    assert populated["links_no_container"] == {
        "relativos": 1, "absolutos_http": 1, "fragmentos": 1, "vazios": 1, "outros": 0
    }
    assert populated["dados_estruturados"]["atributos_data_no_container"] == ["data-kind"]


def test_invalid_site_configuration_and_empty_html() -> None:
    report = diagnose_barcellos('<script>window.SAMI_SITE = {invalid};</script>')
    assert report["dados_estruturados"]["configuracao_site"]["estado"] == "invalido"
    with pytest.raises(ValueError, match="HTML vazio"):
        diagnose_barcellos(" ")


def test_local_diagnostic_does_not_access_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "fixture.html"
    path.write_text(HTML, encoding="utf-8")
    client = Mock(side_effect=AssertionError("HTTP não deve ser chamado"))
    monkeypatch.setattr("src.barcellos_diagnostic.HttpClient", client)
    assert main(["--html-file", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["cards"]["confirmados"] == 0
    client.assert_not_called()


def test_remote_diagnostic_uses_client_and_closes_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings("https://example.com", "AcademicBot/1.0", 5, 0, tmp_path, "INFO")
    client = Mock(spec=HttpClient)
    client.fetch_page.side_effect = [HTML, CollectionError("HTTP 403: secret-fixture")]
    monkeypatch.setattr(Settings, "from_env", Mock(return_value=settings))
    factory = Mock(return_value=client)
    monkeypatch.setattr("src.barcellos_diagnostic.HttpClient", factory)
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["cards"]["confirmados"] == 0
    with caplog.at_level(logging.ERROR):
        assert main([]) == 1
    assert "HTTP 403" in caplog.text
    assert "secret-fixture" not in caplog.text
    assert factory.call_count == client.fetch_page.call_count == client.close.call_count == 2
