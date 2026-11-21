import json
from pathlib import Path

import pytest

from src.barcellos_loading_diagnostic import diagnose_loading, main


BASE = "https://example.com/vendas"
HTML = """
<script>window.SAMI_SITE = {"apiImoveis":"/api/imoveis?token=secret","perPage":16};</script>
<script src="/js/busca.js?token=secret"></script><script src="/js/scroll-infinito.js"></script>
<form id="form-busca"><input name="cidade"><input name="secret">
<div data-dropdown-nome="bairro"></div></form>
"""
SEARCH = "params.set('finalidade', CFG.finalidade); im.code; im.titulo; im.valor;"
SCROLL = """
primeira.set('pagina', 1);
params.set('pagina', state.pagina + 1);
fetch(CFG.apiImoveis + '?' + params.toString(), { headers: { 'Accept': 'application/json' } })
    .then(function (r) { return r.json(); });
data.items; data.tem_mais; data.pagina;
"""


def test_offline_diagnostic_reports_loading_contract_without_secrets() -> None:
    report = diagnose_loading(HTML, SEARCH, SCROLL, BASE)
    api = report["api"]
    assert api["url_sem_query"] == "https://example.com/api/imoveis"
    assert api["query_na_configuracao"] is True
    assert (api["metodo"], api["resposta_esperada"]) == ("GET", "JSON")
    assert api["primeira_pagina"] == 1
    assert api["pagina_seguinte"] == "pagina atual + 1"
    assert api["parametros_do_formulario"] == ["bairro[]", "cidade"]
    assert api["campos_resposta_referenciados"] == ["items", "pagina", "tem_mais"]
    assert api["campos_item_referenciados"] == ["code", "titulo", "valor"]
    assert report["lote_visual_configurado"] == 16
    assert "secret" not in json.dumps(report)


def test_unknown_request_format_is_not_guessed() -> None:
    report = diagnose_loading(HTML, "void 0;", "fetch('/outra', {method:'POST'});", BASE)
    assert report["api"]["metodo"] is None
    assert report["api"]["resposta_esperada"] is None
    assert report["api"]["primeira_pagina"] is None
    assert report["api"]["pagina_seguinte"] is None


@pytest.mark.parametrize("html", ["<html></html>", "<script>window.SAMI_SITE = {invalid};</script>"])
def test_missing_or_invalid_configuration_fails(html: str) -> None:
    with pytest.raises(ValueError, match="SAMI_SITE"):
        diagnose_loading(html, SEARCH, SCROLL, BASE)


def test_cli_reads_local_files_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import socket

    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("Rede não deve ser acessada")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    paths = [tmp_path / "pagina.html", tmp_path / "busca.js", tmp_path / "scroll.js"]
    for path, content in zip(paths, (HTML, SEARCH, SCROLL)):
        path.write_text(content, encoding="utf-8")
    assert main([
        "--html-file", str(paths[0]), "--busca-file", str(paths[1]),
        "--scroll-file", str(paths[2]), "--source-url", BASE,
    ]) == 0
    assert json.loads(capsys.readouterr().out)["api"]["primeira_pagina"] == 1
