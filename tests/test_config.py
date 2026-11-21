from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.config.settings import Settings


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_source_url_fails(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    if value is None:
        monkeypatch.delenv("SOURCE_URL", raising=False)
    else:
        monkeypatch.setenv("SOURCE_URL", value)
    with pytest.raises(ValueError, match="URL da fonte precisa ser configurada em SOURCE_URL"):
        Settings.from_env()


def test_loads_url_from_dotenv_and_respects_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("SOURCE_URL=https://example.com/imoveis\n", encoding="utf-8")
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: load_dotenv(dotenv_path))
    monkeypatch.delenv("SOURCE_URL", raising=False)
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    try:
        assert Settings.from_env().source_url == "https://example.com/imoveis"
        monkeypatch.setenv("SOURCE_URL", "https://example.org/outra")
        assert Settings.from_env().source_url == "https://example.org/outra"
    finally:
        monkeypatch.delenv("SOURCE_URL", raising=False)


def test_default_output_directory_is_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("SOURCE_URL", "https://example.com/imoveis")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    assert Settings.from_env().output_dir == Path("data")


def test_source_selection_loads_from_dotenv_and_respects_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    fixture_path = tmp_path / "exemplo.html"
    dotenv_path.write_text(
        f"PROPERTY_SOURCE=local\nLOCAL_HTML_PATH={fixture_path}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: load_dotenv(dotenv_path))
    for name in ("PROPERTY_SOURCE", "LOCAL_HTML_PATH", "SOURCE_URL"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    settings = Settings.from_env()
    assert settings.property_source == "local"
    assert settings.local_html_path == fixture_path
    assert settings.source_url == ""

    monkeypatch.setenv("PROPERTY_SOURCE", " VIVAREAL ")
    monkeypatch.setenv("SOURCE_URL", "https://example.com/imoveis")
    settings = Settings.from_env()
    assert settings.property_source == "vivareal"
    assert settings.source_url == "https://example.com/imoveis"


def test_source_defaults_to_vivareal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.delenv("PROPERTY_SOURCE", raising=False)
    monkeypatch.setenv("SOURCE_URL", "https://example.com/imoveis")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    assert Settings.from_env().property_source == "vivareal"


def test_barcellos_source_loads_from_dotenv_and_requires_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("PROPERTY_SOURCE=barcellos\nSOURCE_URL=https://example.com/vendas\n", encoding="utf-8")
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: load_dotenv(dotenv_path))
    for name in ("PROPERTY_SOURCE", "SOURCE_URL"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    settings = Settings.from_env()
    assert settings.property_source == "barcellos"
    assert settings.source_url == "https://example.com/vendas"
    monkeypatch.setenv("SOURCE_URL", "")
    with pytest.raises(ValueError, match="SOURCE_URL"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["", "  ", "desconhecida"])
def test_unknown_source_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("PROPERTY_SOURCE", value)
    with pytest.raises(ValueError, match="PROPERTY_SOURCE inválida"):
        Settings.from_env()


@pytest.mark.parametrize("value", [None, "", "  "])
def test_local_source_requires_html_path(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("PROPERTY_SOURCE", "local")
    if value is None:
        monkeypatch.delenv("LOCAL_HTML_PATH", raising=False)
    else:
        monkeypatch.setenv("LOCAL_HTML_PATH", value)
    with pytest.raises(ValueError, match="LOCAL_HTML_PATH"):
        Settings.from_env()


def test_local_source_does_not_validate_source_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.config.settings.load_dotenv", lambda: False)
    monkeypatch.setenv("PROPERTY_SOURCE", "local")
    monkeypatch.setenv("LOCAL_HTML_PATH", "fixture.html")
    monkeypatch.setenv("SOURCE_URL", "url inválida")
    monkeypatch.setenv("USER_AGENT", "AcademicBot/1.0")
    assert Settings.from_env().property_source == "local"
