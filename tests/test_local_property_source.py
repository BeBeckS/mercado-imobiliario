import socket
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from src.collectors.local_source import LocalPropertySource
from src.collectors.property_parser import PropertyParseError
from src.collectors.property_source import PropertySource
from src.config.settings import Settings
from src.models.property import Property
from src.utils.persistence import load_properties

FIXTURE = Path(__file__).parent / "fixtures" / "imoveis_locais_sinteticos.html"
BASE_URL = "https://example.com/busca"


def test_local_source_reads_fixture_and_returns_properties() -> None:
    source: PropertySource = LocalPropertySource(FIXTURE, base_url=BASE_URL)
    properties = source.collect()
    assert len(properties) == 2
    assert all(isinstance(prop, Property) for prop in properties)
    first, second = properties
    assert (first.id_anuncio, first.titulo) == ("local-101", "Apartamento no bairro fictício")
    assert (first.preco, first.moeda, first.area_m2) == (Decimal("450000"), "BRL", Decimal("85.5"))
    assert (first.quartos, first.banheiros, first.vagas) == (2, 2, 1)
    assert (first.endereco, first.bairro, first.cidade, first.estado) == (
        "Rua Fictícia, 100", "Bairro Exemplo", "Porto Alegre", "RS"
    )
    assert first.url == "https://example.com/imovel/local-101"
    assert first.fonte == "Fixture local"
    assert (second.id_anuncio, second.preco, second.area_m2) == (
        "local-202", Decimal("275000"), Decimal("48")
    )
    assert second.vagas is None and second.bairro is None


def test_local_source_retains_relative_urls_without_base() -> None:
    properties = LocalPropertySource(FIXTURE).collect()
    assert properties[0].url == "/imovel/local-101"


def test_local_source_reports_missing_or_invalid_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        LocalPropertySource(tmp_path / "ausente.html").collect()
    invalid = tmp_path / "invalido.html"
    invalid.write_text("   ", encoding="utf-8")
    with pytest.raises(PropertyParseError, match="HTML vazio"):
        LocalPropertySource(invalid).collect()


def test_local_source_runs_full_pipeline_without_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(BASE_URL, "AcademicBot/1.0", 5, 0, tmp_path, "INFO")
    source = LocalPropertySource(FIXTURE, base_url=BASE_URL)
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    monkeypatch.setattr(main, "VivaRealSource", Mock(return_value=source))
    http = Mock(side_effect=AssertionError("HTTP não deve ser chamado"))
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", http)

    assert main.main() == 0
    assert "2 imóvel(is)" in capsys.readouterr().out
    persisted = load_properties(tmp_path / "properties.json")
    assert persisted == source.collect()
    assert [prop.id_anuncio for prop in persisted] == ["local-101", "local-202"]
    assert all(prop.fonte == "Fixture local" for prop in persisted)
    http.assert_not_called()


def test_synthetic_local_sample_runs_full_pipeline_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def deny_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("A validação local não deve acessar a rede")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)

    settings = Settings(BASE_URL, "AcademicBot/1.0", 5, 0, tmp_path, "INFO")
    source: PropertySource = LocalPropertySource(FIXTURE)
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    source_factory = Mock(return_value=source)
    monkeypatch.setattr(main, "VivaRealSource", source_factory)
    http = Mock(side_effect=AssertionError("HTTP não deve ser chamado"))
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", http)
    save = Mock(wraps=main.save_properties)
    monkeypatch.setattr(main, "save_properties", save)

    assert main.main() == 0
    path = tmp_path / "properties.json"
    assert capsys.readouterr().out == f"Coleta concluída: 2 imóvel(is). Arquivo: {path}\n"
    source_factory.assert_called_once_with(settings)
    http.assert_not_called()
    save.assert_called_once()
    collected = save.call_args.args[0]
    assert save.call_args.args[1] == path
    assert isinstance(collected, list) and len(collected) == 2
    assert all(isinstance(prop, Property) for prop in collected)

    persisted = load_properties(path)
    assert len(persisted) == 2
    assert all(prop.id_anuncio is not None for prop in persisted)
    assert len({prop.id_anuncio for prop in persisted}) == 2
    assert persisted == collected
    assert [prop.vagas for prop in persisted] == [1, None]
    assert all(prop.fonte == "Fixture local" for prop in persisted)
