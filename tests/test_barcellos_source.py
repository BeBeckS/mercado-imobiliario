import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call

import pytest

import main
from src.collectors.barcellos_parser import parse_barcellos_item, parse_barcellos_page
from src.collectors.barcellos_source import BarcellosSource
from src.collectors.http_client import CollectionError, HttpClient
from src.config.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_URL = "https://example.com/vendas"


@pytest.fixture
def pages() -> list[dict[str, Any]]:
    return [
        json.loads((FIXTURES / f"barcellos_pagina_{page}.json").read_text(encoding="utf-8"),
                   parse_float=Decimal)
        for page in (1, 2, 3)
    ]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(SOURCE_URL, "AcademicBot/1.0", 5, 0, tmp_path, "INFO",
                    property_source="barcellos")


def test_parser_maps_decimal_numbers_null_and_consult_price(pages: list[dict[str, Any]]) -> None:
    first = parse_barcellos_item(pages[0]["items"][0], SOURCE_URL)
    assert first.id_anuncio == "101"
    assert (first.titulo, first.tipo_imovel, first.bairro, first.cidade, first.estado) == (
        "Apartamento ficticio", "Apartamento", "Centro", "Cidade Exemplo", "RS"
    )
    assert (first.area_m2, first.quartos, first.banheiros, first.vagas) == (Decimal("85.75"), 2, 2, 1)
    assert first.preco == Decimal("450000") and first.moeda == "BRL"
    assert first.url == "https://example.com/vendas/v00101-apartamento"
    assert first.endereco is None and first.localizacao is None

    consult = parse_barcellos_item(pages[1]["items"][1], SOURCE_URL)
    assert consult.bairro is None and consult.area_m2 is None and consult.quartos is None
    assert consult.preco == Decimal("999000")


@pytest.mark.parametrize(("field", "value"), [
    ("code", None), ("code", 101), ("titulo", 42), ("area", "85,5"),
    ("area", float("nan")), ("dormitorios", True), ("banheiros", 1.5),
    ("valor", True), ("sob_consulta", "sim"), ("url", "//other.example/vendas/1"),
    ("url", "javascript:alert(1)"), ("url", "/vendas/endereco com espaco"),
])
def test_parser_rejects_unreliable_values(
    pages: list[dict[str, Any]], field: str, value: Any
) -> None:
    item = deepcopy(pages[0]["items"][0])
    item[field] = value
    with pytest.raises(ValueError):
        parse_barcellos_item(item, SOURCE_URL)


@pytest.mark.parametrize(("field", "value"), [
    ("status", "ERRO"), ("finalidade", "aluguel"), ("pagina", 2), ("pagina", True),
    ("tem_mais", "true"), ("total", False), ("truncado", True), ("truncado", "sim"),
    ("truncado", None), ("items", {}), ("items", [None]), ("count", 99), ("count", None),
])
def test_page_rejects_invalid_contract(
    pages: list[dict[str, Any]], field: str, value: Any
) -> None:
    page = deepcopy(pages[0])
    page[field] = value
    with pytest.raises(ValueError):
        parse_barcellos_page(page, SOURCE_URL, 1)


def test_paginated_source_deduplicates_and_stops_at_last_page(
    settings: Settings, pages: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Mock()
    client.fetch_json.side_effect = pages
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))

    properties = BarcellosSource(settings).collect()

    assert [prop.id_anuncio for prop in properties] == ["101", "102", "103", "104"]
    assert properties[1].titulo == "Casa ficticia" and properties[1].preco == Decimal("260000")
    assert client.fetch_json.call_args_list == [
        call(f"https://example.com/api/imoveis.php?finalidade=vendas&pagina={page}")
        for page in (1, 2, 3)
    ]
    client.close.assert_called_once_with()


@pytest.mark.parametrize(("unique", "duplicates", "valid"), [(10, 0, True), (1, 0, False), (1, 9, False)])
def test_final_total_uses_distinct_ids(
    settings: Settings, pages: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
    unique: int, duplicates: int, valid: bool
) -> None:
    page = deepcopy(pages[0])
    item = page["items"][0]
    page["items"] = [{**item, "code": str(i)} for i in range(unique)]
    page["items"].extend([deepcopy(page["items"][0]) for _ in range(duplicates)])
    page.update(total=10, count=len(page["items"]), tem_mais=False)
    client = Mock()
    client.fetch_json.return_value = page
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))

    if valid:
        assert len(BarcellosSource(settings).collect()) == 10
    else:
        with pytest.raises(ValueError, match="Paginação Barcellos incompleta.*10.*1"):
            BarcellosSource(settings).collect()
    client.fetch_json.assert_called_once()
    client.close.assert_called_once_with()


def test_changed_total_stops_pagination(
    settings: Settings, pages: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    pages[1]["total"] = pages[0]["total"] + 1
    client = Mock()
    client.fetch_json.side_effect = pages
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))
    with pytest.raises(ValueError, match="total mudou"):
        BarcellosSource(settings).collect()
    assert client.fetch_json.call_count == 2
    client.close.assert_called_once_with()


def test_incomplete_final_page_preserves_existing_data(
    settings: Settings, pages: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture
) -> None:
    path = settings.output_dir / "properties.json"
    original = b'[]\n'
    path.write_bytes(original)
    page = deepcopy(pages[0])
    page.update(total=10, count=1, tem_mais=False, items=page["items"][:1])
    client = Mock()
    client.fetch_json.return_value = page
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))

    assert main.main() == 1
    assert "Paginação Barcellos incompleta" in caplog.text
    assert path.read_bytes() == original
    assert not (settings.output_dir / "snapshots").exists()
    client.close.assert_called_once_with()


@pytest.mark.parametrize("problem", ["repeated", "empty", "wrong_page", "total"])
def test_pagination_failure_is_explicit_and_closes_client(
    settings: Settings, pages: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    second = deepcopy(pages[1])
    if problem == "repeated":
        second["items"] = deepcopy(pages[0]["items"])
    elif problem == "empty":
        second["items"], second["count"] = [], 0
    elif problem == "wrong_page":
        second["pagina"] = 1
    else:
        pages[0]["total"] = 2
    client = Mock()
    client.fetch_json.side_effect = [pages[0], second]
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))

    with pytest.raises(ValueError, match="Página|Paginação"):
        BarcellosSource(settings).collect()
    assert client.fetch_json.call_count == (1 if problem == "total" else 2)
    client.close.assert_called_once_with()


def test_http_error_closes_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Mock()
    client.fetch_json.side_effect = CollectionError("HTTP 403")
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))
    with pytest.raises(CollectionError, match="HTTP 403"):
        BarcellosSource(settings).collect()
    client.close.assert_called_once_with()


def test_barcellos_full_flow_http_to_persistence_and_summary(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    client = HttpClient(settings)
    client.session.get = Mock(side_effect=[
        Mock(status_code=200, text="User-agent: *\nAllow: /", headers={}),
        *[
            Mock(status_code=200,
                 text=(FIXTURES / f"barcellos_pagina_{page}.json").read_text(encoding="utf-8"),
                 headers={"Content-Type": "application/json; charset=utf-8"})
            for page in (1, 2, 3)
        ],
    ])
    original_close = client.close
    client.close = Mock(side_effect=original_close)
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))
    source_factory = Mock(wraps=BarcellosSource)
    monkeypatch.setattr(main, "BarcellosSource", source_factory)
    monkeypatch.setattr(main, "VivaRealSource", Mock(side_effect=AssertionError("fonte incorreta")))
    monkeypatch.setattr(main, "LocalPropertySource", Mock(side_effect=AssertionError("fonte incorreta")))

    assert main.main() == 0
    source_factory.assert_called_once_with(settings)
    assert "Coleta concluída: 4 imóvel(is)" in capsys.readouterr().out
    records = json.loads((settings.output_dir / "properties.json").read_text(encoding="utf-8"))
    assert [item["id_anuncio"] for item in records] == ["101", "102", "103", "104"]
    assert records[0]["area_m2"] == "85.75"
    assert records[2]["preco"] == "999000"
    assert records[0]["url"] == "https://example.com/vendas/v00101-apartamento"
    snapshot_path, = (settings.output_dir / "snapshots").glob("*.json")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["fonte"] == "barcellos" and snapshot["quantidade"] == 4
    assert snapshot["imoveis"] == records
    assert client.session.get.call_count == 4
    client.close.assert_called_once_with()


def test_invalid_second_page_does_not_persist_partial_collection(
    settings: Settings, pages: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    client = Mock()
    pages[1]["tem_mais"] = "true"
    client.fetch_json.side_effect = pages[:2]
    monkeypatch.setattr("src.collectors.barcellos_source.HttpClient", Mock(return_value=client))

    assert main.main() == 1
    assert "tem_mais" in caplog.text
    assert not (settings.output_dir / "properties.json").exists()
    client.close.assert_called_once_with()
