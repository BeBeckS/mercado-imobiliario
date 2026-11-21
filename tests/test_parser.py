import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.collectors.http_client import CollectionError
from src.collectors.parser import parse_listings

BASE_URL = "https://example.com/busca/"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "vivareal_exemplo.html"


def test_parse_fixture_and_missing_fields() -> None:
    listings = parse_listings(FIXTURE.read_text(encoding="utf-8"), BASE_URL, NOW)
    assert len(listings) == 2
    first, second = listings
    assert first.id_anuncio == "12345"
    assert first.titulo == "Apartamento no Centro"
    assert first.preco == Decimal("450000")
    assert first.area_m2 == Decimal("85.5")
    assert (first.quartos, first.banheiros, first.vagas) == (2, 1, 1)
    assert (first.bairro, first.cidade, first.endereco) == ("Centro", "Porto Alegre", "Rua Exemplo, 100")
    assert str(first.url) == "https://example.com/imovel/apartamento-12345/"
    assert second.preco is None and second.url is None and second.bairro is None


def test_duplicate_records_are_removed_and_hash_ignores_collection_time() -> None:
    item = {"@type": "Apartment", "identifier": "1", "offers": {"price": "10,00"}}
    equivalent = item | {"offers": {"price": "10.0"}}
    html = '<script type="application/ld+json">' + json.dumps([item, equivalent]) + "</script>"
    first = parse_listings(html, BASE_URL, NOW)
    later = parse_listings(html, BASE_URL, datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert len(first) == 1
    assert first[0].hash_registro == later[0].hash_registro


@pytest.mark.parametrize("html", [
    "<html>CAPTCHA</html>",
    '<script type="application/ld+json">{invalid}</script>',
    '<script type="application/ld+json">{"@type":"Apartment","offers":{"priceCurrency":"USD","price":100}}</script>',
])
def test_unrecognized_or_invalid_page_fails(html: str) -> None:
    with pytest.raises(CollectionError):
        parse_listings(html, BASE_URL, NOW)
