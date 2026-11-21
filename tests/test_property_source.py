import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from src.collectors.http_client import CollectionError
from src.collectors.property_parser import PropertyParseError
from src.collectors.property_source import PropertySource
from src.collectors.vivareal_source import VivaRealSource
from src.config.settings import Settings
from src.models.property import Property

FIXTURE = Path(__file__).parent / "fixtures" / "diagnostico_exemplo.html"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings("https://example.com/busca", "AcademicBot/1.0", 5, 0, tmp_path, "INFO")


def test_source_fetches_and_passes_configured_origin_to_parser(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    client = Mock()
    client.fetch_page.return_value = html
    factory = Mock(return_value=client)
    expected = [Property(id_anuncio="123", fonte="VivaReal")]
    parser = Mock(return_value=expected)
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", factory)
    monkeypatch.setattr("src.collectors.vivareal_source.parse_properties", parser)

    source: PropertySource = VivaRealSource(settings)
    assert source.collect() == expected
    factory.assert_called_once_with(settings)
    client.fetch_page.assert_called_once_with()
    client.close.assert_called_once_with()
    parser.assert_called_once_with(html, base_url=settings.source_url, source="VivaReal")


def test_source_closes_client_and_propagates_http_errors(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Mock()
    client.fetch_page.side_effect = CollectionError("HTTP 403")
    parser = Mock()
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", Mock(return_value=client))
    monkeypatch.setattr("src.collectors.vivareal_source.parse_properties", parser)
    with pytest.raises(CollectionError, match="HTTP 403"):
        VivaRealSource(settings).collect()
    client.close.assert_called_once_with()
    parser.assert_not_called()


def test_source_closes_client_before_parser_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = Mock()
    client.fetch_page.return_value = "html inválido"
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", Mock(return_value=client))
    monkeypatch.setattr(
        "src.collectors.vivareal_source.parse_properties",
        Mock(side_effect=PropertyParseError("HTML inválido")),
    )
    with pytest.raises(PropertyParseError):
        VivaRealSource(settings).collect()
    client.close.assert_called_once_with()


def test_pipeline_accepts_another_source_without_http(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeSource:
        def collect(self) -> list[Property]:
            return [Property(id_anuncio="fixture-1", titulo="Exemplo", fonte="Teste")]

    source: PropertySource = FakeSource()
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    monkeypatch.setattr(main, "VivaRealSource", Mock(return_value=source))
    client_factory = Mock(side_effect=AssertionError("HTTP não deve ser usado"))
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", client_factory)
    assert main.main() == 0
    data = json.loads((settings.output_dir / "properties.json").read_text(encoding="utf-8"))
    assert [(item["id_anuncio"], item["fonte"]) for item in data] == [("fixture-1", "Teste")]
    client_factory.assert_not_called()
