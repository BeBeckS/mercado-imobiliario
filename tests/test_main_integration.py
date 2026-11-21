import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from src.collectors.http_client import CollectionError
from src.collectors.local_source import LocalPropertySource
from src.collectors.property_parser import PropertyParseError
from src.config.settings import Settings
from src.models.property import Property

FIXTURE = Path(__file__).parent / "fixtures" / "diagnostico_exemplo.html"


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, Mock]:
    settings = Settings("https://example.com/busca", "AcademicBot/1.0", 5, 0, tmp_path, "INFO")
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=settings))
    client = Mock()
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", Mock(return_value=client))
    return settings, client


def test_full_flow_with_mocked_parser(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, client = configured
    html = FIXTURE.read_text(encoding="utf-8")
    client.fetch_page.return_value = html
    parser = Mock(return_value=[Property(titulo="Teste", id_anuncio="123", fonte="VivaReal")])
    monkeypatch.setattr("src.collectors.vivareal_source.parse_properties", parser)

    assert main.main() == 0
    parser.assert_called_once_with(html, base_url=settings.source_url, source="VivaReal")
    client.fetch_page.assert_called_once_with()
    client.close.assert_called_once_with()
    assert "1 imóvel(is)" in capsys.readouterr().out
    path = settings.output_dir / "properties.json"
    assert path.is_file()
    record = json.loads(path.read_text(encoding="utf-8"))[0]
    assert record["titulo"] == "Teste" and record["id_anuncio"] == "123"
    assert record == Property(titulo="Teste", id_anuncio="123", fonte="VivaReal").model_dump(mode="json")


def test_fixture_flows_through_real_parser_and_persistence(configured: tuple[Settings, Mock]) -> None:
    settings, client = configured
    client.fetch_page.return_value = FIXTURE.read_text(encoding="utf-8")
    assert main.main() == 0
    path = settings.output_dir / "properties.json"
    assert path.is_file()
    properties = json.loads(path.read_text(encoding="utf-8"))
    assert len(properties) == 2
    assert [prop["id_anuncio"] for prop in properties] == ["123", "456"]
    assert properties[0]["area_m2"] == "85" and properties[0]["moeda"] == "BRL"
    assert properties[0]["localizacao"] == "Centro, Porto Alegre"
    client.close.assert_called_once()


def test_two_runs_update_and_preserve_previous_properties(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, client = configured
    client.fetch_page.return_value = "<html></html>"
    parser = Mock(side_effect=[
        [Property(id_anuncio="1", titulo="Antes"), Property(id_anuncio="2", titulo="Antigo")],
        [Property(id_anuncio="1", titulo="Depois")],
    ])
    monkeypatch.setattr("src.collectors.vivareal_source.parse_properties", parser)
    assert main.main() == 0
    assert main.main() == 0
    records = json.loads((settings.output_dir / "properties.json").read_text(encoding="utf-8"))
    assert [(record["id_anuncio"], record["titulo"]) for record in records] == [
        ("1", "Depois"), ("2", "Antigo")
    ]
    snapshots = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (settings.output_dir / "snapshots").glob("*.json")
    ]
    assert len(snapshots) == 2
    by_count = {snapshot["quantidade"]: snapshot for snapshot in snapshots}
    assert [prop["titulo"] for prop in by_count[2]["imoveis"]] == ["Antes", "Antigo"]
    assert [prop["titulo"] for prop in by_count[1]["imoveis"]] == ["Depois"]
    assert all(snapshot["fonte"] == "vivareal" and snapshot["data_coleta"] for snapshot in snapshots)


def test_invalid_existing_data_is_reported_without_overwrite(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings, client = configured
    path = settings.output_dir / "properties.json"
    path.write_text("{inválido", encoding="utf-8")
    client.fetch_page.return_value = "<html></html>"
    monkeypatch.setattr(
        "src.collectors.vivareal_source.parse_properties", Mock(return_value=[Property(id_anuncio="1")])
    )
    assert main.main() == 1
    assert "Arquivo de imóveis inválido" in caplog.text
    assert "Snapshot preservado" in caplog.text
    assert path.read_text(encoding="utf-8") == "{inválido"
    assert len(list((settings.output_dir / "snapshots").glob("*.json"))) == 1
    client.close.assert_called_once()


def test_snapshot_failure_does_not_update_consolidated(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings, client = configured
    path = settings.output_dir / "properties.json"
    path.write_text("[]", encoding="utf-8")
    client.fetch_page.return_value = "<html></html>"
    monkeypatch.setattr(
        "src.collectors.vivareal_source.parse_properties", Mock(return_value=[Property(id_anuncio="1")])
    )
    monkeypatch.setattr(main, "save_property_snapshot", Mock(side_effect=OSError("snapshot indisponível")))
    assert main.main() == 1
    assert "snapshot indisponível" in caplog.text
    assert path.read_text(encoding="utf-8") == "[]"


def test_no_properties_is_reported_without_output(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings, client = configured
    client.fetch_page.return_value = "<html><body>Sem anúncios</body></html>"
    parser = Mock(return_value=[])
    monkeypatch.setattr("src.collectors.vivareal_source.parse_properties", parser)
    assert main.main() == 1
    assert "Nenhum imóvel" in caplog.text
    assert not list(settings.output_dir.iterdir())
    client.close.assert_called_once()


@pytest.mark.parametrize(("error", "expected"), [
    ("HTTP 403", "fonte recusou a requisição"),
    ("HTTP 500", "HTTP 500"),
    ("Timeout ao acessar a fonte", "Timeout"),
    ("Erro de conexão ao acessar a fonte", "Erro de conexão"),
])
def test_collection_errors_close_client(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, error: str, expected: str,
) -> None:
    settings, client = configured
    client.fetch_page.side_effect = CollectionError(error)
    parser = Mock()
    monkeypatch.setattr("src.collectors.vivareal_source.parse_properties", parser)
    assert main.main() == 1
    assert expected in caplog.text
    assert not list(settings.output_dir.iterdir())
    parser.assert_not_called()
    client.close.assert_called_once()


def test_invalid_html_is_reported_and_client_closed(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings, client = configured
    client.fetch_page.return_value = "html inválido"
    monkeypatch.setattr(
        "src.collectors.vivareal_source.parse_properties", Mock(side_effect=PropertyParseError("HTML inválido"))
    )
    assert main.main() == 1
    assert "HTML inválido" in caplog.text
    assert not list(settings.output_dir.iterdir())
    client.close.assert_called_once()


@pytest.mark.parametrize("error", ["A URL da fonte precisa ser configurada em SOURCE_URL.", "SOURCE_URL inválida."])
def test_configuration_errors_do_not_create_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: str
) -> None:
    monkeypatch.setattr(main.Settings, "from_env", Mock(side_effect=ValueError(error)))
    client_factory = Mock()
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", client_factory)
    assert main.main() == 1
    assert error in caplog.text
    client_factory.assert_not_called()


@pytest.mark.parametrize("url", ["", "invalid-url"])
def test_real_settings_reject_invalid_url_without_creating_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, url: str
) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("SOURCE_URL", url)
    client_factory = Mock()
    monkeypatch.setattr("src.collectors.vivareal_source.HttpClient", client_factory)
    assert main.main() == 1
    assert "URL" in caplog.text
    client_factory.assert_not_called()


def test_403_remains_visible_with_critical_log_level(
    configured: tuple[Settings, Mock], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings, client = configured
    critical_settings = Settings(
        settings.source_url, settings.user_agent, settings.timeout_seconds,
        settings.interval_seconds, settings.output_dir, "CRITICAL",
    )
    monkeypatch.setattr(main.Settings, "from_env", Mock(return_value=critical_settings))
    client.fetch_page.side_effect = CollectionError("HTTP 403")
    assert main.main() == 1
    assert "fonte recusou a requisição" in caplog.text
    client.close.assert_called_once()


def test_configured_local_source_runs_pipeline_without_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("PROPERTY_SOURCE", "local")
    monkeypatch.setenv("LOCAL_HTML_PATH", str(FIXTURE))
    monkeypatch.setenv("SOURCE_URL", "inválida e não utilizada")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    local_factory = Mock(wraps=LocalPropertySource)
    remote_factory = Mock(side_effect=AssertionError("A fonte remota não deve ser criada"))
    monkeypatch.setattr(main, "LocalPropertySource", local_factory)
    monkeypatch.setattr(main, "VivaRealSource", remote_factory)

    assert main.main() == 0
    local_factory.assert_called_once_with(FIXTURE)
    remote_factory.assert_not_called()
    assert "2 imóvel(is)" in capsys.readouterr().out
    records = json.loads((tmp_path / "properties.json").read_text(encoding="utf-8"))
    assert len(records) == 2
    assert all(record["fonte"] == "Fixture local" for record in records)


def test_configured_vivareal_is_selected_at_composition_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("PROPERTY_SOURCE", "vivareal")
    monkeypatch.setenv("SOURCE_URL", "https://example.com/busca")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    source = Mock()
    source.collect.return_value = [Property(id_anuncio="123")]
    remote_factory = Mock(return_value=source)
    local_factory = Mock(side_effect=AssertionError("A fonte local não deve ser criada"))
    monkeypatch.setattr(main, "VivaRealSource", remote_factory)
    monkeypatch.setattr(main, "LocalPropertySource", local_factory)

    assert main.main() == 0
    remote_factory.assert_called_once()
    assert remote_factory.call_args.args[0].property_source == "vivareal"
    source.collect.assert_called_once_with()
    local_factory.assert_not_called()
    assert (tmp_path / "properties.json").is_file()


def test_unknown_source_fails_before_constructing_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("PROPERTY_SOURCE", "desconhecida")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    remote_factory = Mock()
    local_factory = Mock()
    monkeypatch.setattr(main, "VivaRealSource", remote_factory)
    monkeypatch.setattr(main, "LocalPropertySource", local_factory)

    assert main.main() == 1
    assert "PROPERTY_SOURCE inválida" in caplog.text
    remote_factory.assert_not_called()
    local_factory.assert_not_called()
    assert not list(tmp_path.iterdir())
