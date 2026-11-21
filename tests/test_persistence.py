import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.models.property import Property
from src.utils.persistence import (
    PersistenceDataError, load_properties, save_properties, save_property_snapshot,
)
from src.utils.property_identity import same_property


def test_missing_file_returns_empty_without_creating_directory(tmp_path: Path) -> None:
    path = tmp_path / "novo" / "properties.json"
    assert load_properties(path) == []
    assert not path.parent.exists()


def test_write_and_read_properties_creates_directory(tmp_path: Path) -> None:
    path = tmp_path / "novo" / "properties.json"
    expected = [
        Property(id_anuncio="1", titulo="Apartamento São João", preco=Decimal("1234.50"), area_m2=Decimal("85.5")),
        Property(id_anuncio="2", quartos=0, url="https://example.com/imovel/2", fonte="Exemplo"),
    ]
    assert save_properties(expected, path) == path
    assert load_properties(path) == expected
    content = path.read_text(encoding="utf-8")
    assert "São João" in content
    assert "\n  {" in content
    assert json.loads(content) == [prop.model_dump(mode="json") for prop in expected]


def test_empty_list_creates_valid_file_and_preserves_existing(tmp_path: Path) -> None:
    path = tmp_path / "properties.json"
    save_properties([], path)
    assert json.loads(path.read_text(encoding="utf-8")) == []
    prop = Property(id_anuncio="1")
    save_properties([prop], path)
    save_properties([], path)
    assert load_properties(path) == [prop]


@pytest.mark.parametrize("data", [
    b"{bad",
    b"{}",
    b'[{"preco": -1}]',
    b'[{"campo_desconhecido": 1}]',
    b"\xff",
])
def test_invalid_file_raises_specific_error_and_is_not_overwritten(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "properties.json"
    path.write_bytes(data)
    with pytest.raises(PersistenceDataError, match="Arquivo de imóveis inválido"):
        load_properties(path)
    with pytest.raises(PersistenceDataError):
        save_properties([Property(id_anuncio="1")], path)
    assert path.read_bytes() == data


def test_update_deduplicates_and_preserves_old_data(tmp_path: Path) -> None:
    path = tmp_path / "properties.json"
    old = Property(id_anuncio="1", titulo="Antigo", preco=Decimal("100"), bairro="Centro", quartos=2)
    retained = Property(id_anuncio="2", titulo="Não apareceu")
    save_properties([old, retained], path)
    new = Property(id_anuncio="1", titulo="Atualizado", preco=Decimal("150"), quartos=0)
    added = Property(id_anuncio="3", titulo="Novo")
    save_properties([new, added, added], path)
    results = load_properties(path)
    assert len(results) == 3
    assert results[0].titulo == "Atualizado" and results[0].preco == Decimal("150")
    assert results[0].bairro == "Centro" and results[0].quartos == 0
    assert results[1:] == [retained, added]


def test_deduplication_uses_same_url_and_content_identity_as_parser(tmp_path: Path) -> None:
    path = tmp_path / "properties.json"
    old = Property(url="https://example.com/imovel/123", titulo="Apto")
    content_only = Property(titulo="Sem ID", preco=Decimal("99"))
    save_properties([old, content_only], path)
    save_properties([
        Property(id_anuncio="123", preco=Decimal("100")),
        content_only,
    ], path)
    result = load_properties(path)
    assert len(result) == 2
    assert result[0].id_anuncio == "123" and result[0].preco == Decimal("100")
    assert result[0].url == old.url
    assert result[1] == content_only


def test_identity_requires_the_same_source_and_keeps_url_rules(tmp_path: Path) -> None:
    first = Property(id_anuncio="123", fonte="Barcellos")
    same = Property(id_anuncio="123", fonte=" barcellos ")
    other = Property(id_anuncio="123", fonte="Outra fonte")
    unknown = Property(id_anuncio="123")
    assert same_property(first, same)
    assert not same_property(first, other)
    assert not same_property(first, unknown)

    with_url = Property(id_anuncio="123", fonte="Barcellos", url="https://example.com/imovel/123")
    url_only = Property(fonte="Barcellos", url="https://example.com/imovel/123")
    other_host = Property(id_anuncio="123", fonte="Barcellos", url="https://other.example/imovel/123")
    other_source_url = Property(fonte="Outra fonte", url=with_url.url)
    assert same_property(with_url, url_only)
    assert not same_property(with_url, other_host)
    assert not same_property(with_url, other_source_url)

    path = tmp_path / "properties.json"
    save_properties([first, other, unknown], path)
    save_properties([same], path)
    assert len(load_properties(path)) == 3


def test_snapshots_preserve_each_collection_and_metadata(tmp_path: Path) -> None:
    collected_at = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    first = [Property(id_anuncio="1", fonte="Barcellos", titulo="Antes")]
    second = [Property(id_anuncio="2", fonte="Barcellos", titulo="Depois")]
    first_path = save_property_snapshot(first, tmp_path, "barcellos", collected_at)
    original = first_path.read_bytes()
    second_path = save_property_snapshot(second, tmp_path, "barcellos", collected_at)

    assert first_path != second_path and first_path.read_bytes() == original
    first_snapshot = json.loads(original)
    assert first_snapshot == {
        "data_coleta": "2026-01-01T12:30:00+00:00",
        "fonte": "barcellos",
        "quantidade": 1,
        "imoveis": [first[0].model_dump(mode="json")],
    }
    assert json.loads(second_path.read_text(encoding="utf-8"))["imoveis"] == [
        second[0].model_dump(mode="json")
    ]


def test_snapshot_write_failure_does_not_leave_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.utils.persistence.os.replace", Mock(side_effect=OSError("falha simulada")))
    with pytest.raises(OSError, match="falha simulada"):
        save_property_snapshot([Property(id_anuncio="1")], tmp_path, "barcellos", datetime.now(timezone.utc))
    assert not list((tmp_path / "snapshots").iterdir())


def test_snapshot_rejects_timestamp_without_timezone(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fuso horário"):
        save_property_snapshot([], tmp_path, "barcellos", datetime(2026, 1, 1))
    assert not list(tmp_path.iterdir())


def test_conflicting_identity_does_not_change_file(tmp_path: Path) -> None:
    path = tmp_path / "properties.json"
    save_properties([
        Property(id_anuncio="1", url="https://example.com/imovel/a"),
        Property(id_anuncio="2", url="https://example.com/imovel/a"),
    ], path)
    old_content = path.read_bytes()
    with pytest.raises(ValueError, match="Identificadores conflitantes"):
        save_properties([Property(url="https://example.com/imovel/a")], path)
    assert path.read_bytes() == old_content


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_write_preserves_previous_file_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = tmp_path / "properties.json"
    save_properties([Property(id_anuncio="1", titulo="Antes")], path)
    original = path.read_bytes()
    monkeypatch.setattr(f"src.utils.persistence.os.{operation}", Mock(side_effect=OSError("falha simulada")))
    with pytest.raises(OSError, match="falha simulada"):
        save_properties([Property(id_anuncio="1", titulo="Depois")], path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
