import json
import socket
from pathlib import Path

import pytest

from src.barcellos_contract_diagnostic import diagnose_contract, main


FIRST = {
    "status": "SUCESSO", "pagina": 1, "tem_mais": True,
    "filtros": {"cidades": ["Cidade fictícia"], "caracteristicas": [{"valor": "x", "rotulo": "y"}]},
    "items": [
        {
            "code": "123", "url": "/vendas/v00123-exemplo", "valor": 1234,
            "valor_formatado": "R$ 1.234,00", "area": "87,5", "dormitorios": 2,
            "suites": 1, "banheiros": 2, "garagens": 1, "sob_consulta": True,
            "condominio": "", "secret": "secret-fixture",
        },
        {"code": "123", "url": "/vendas/v00123-exemplo", "valor": "inválido", "area": None},
        {"code": None, "url": "", "valor": None, "area": ""},
    ],
}
SECOND = {
    "status": "SUCESSO", "pagina": 2, "tem_mais": False,
    "items": [{"code": "123", "url": "/vendas/v00123-exemplo"}, {"code": "456", "url": "/vendas/v00456-outro"}],
}


def test_contract_classifies_fields_identity_and_pagination_without_values() -> None:
    report = diagnose_contract(FIRST, SECOND)
    assert report["raiz"]["items"] == "array"
    assert report["lista"]["quantidade"] == 3
    fields = report["lista"]["campos"]
    assert fields["area"] == {"tipos": {"null": 1, "string": 2}, "ausentes": 0, "nulos": 1, "vazios": 1}
    assert fields["condominio"]["ausentes"] == 2 and fields["condominio"]["vazios"] == 1
    assert report["conversoes"]["valor"] == {
        "validos": 1, "ausentes": 0, "nulos": 1, "vazios": 0, "invalidos": 1, "zeros": 0
    }
    assert report["conversoes"]["area"]["validos"] == 1
    assert report["identidade"]["codigos_distintos"] == 1
    assert report["identidade"]["codigos_preenchidos"] == 2
    assert report["urls"]["relativas"] == 2
    assert report["urls"]["slug_prefixo_codigo"] == 2
    assert report["paginacao"]["codigos_em_ambas"] == 1
    assert report["paginacao"]["urls_em_ambas"] == 1
    assert report["sob_consulta_com_valor_nao_zero"] == 1
    assert report["filtros"]["caracteristicas"]["chaves_objetos"] == ["rotulo", "valor"]
    output = json.dumps(report)
    assert "secret-fixture" not in output
    assert "v00123-exemplo" not in output


@pytest.mark.parametrize("payload", [
    None, {"status": "ERRO", "items": []}, {"status": "SUCESSO", "items": "inválido"},
    {"status": "SUCESSO", "items": [None]}, {"status": "SUCESSO", "items": [], "pagina": {"secret": "valor"}},
])
def test_invalid_response_is_rejected(payload: object) -> None:
    with pytest.raises(ValueError):
        diagnose_contract(payload)


def test_cli_is_offline_and_reports_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("Rede não deve ser acessada")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(FIRST), encoding="utf-8")
    second.write_text(json.dumps(SECOND), encoding="utf-8")
    assert main(["--json-file", str(first), "--second-json-file", str(second)]) == 0
    assert json.loads(capsys.readouterr().out)["paginacao"]["pagina_seguinte"] == 2
    first.write_text("{inválido", encoding="utf-8")
    assert main(["--json-file", str(first)]) == 1
