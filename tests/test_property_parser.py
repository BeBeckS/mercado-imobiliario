import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.collectors.property_parser import PropertyParseError, parse_properties
from src.models.property import Property

FIXTURE = Path(__file__).parent / "fixtures" / "diagnostico_exemplo.html"
BASE_URL = "https://example.com/busca/"


def test_diagnostic_fixture_combines_structures_and_deduplicates() -> None:
    properties = parse_properties(FIXTURE.read_text(encoding="utf-8"), BASE_URL, source="VivaReal")
    assert len(properties) == 2
    first, second = properties
    assert isinstance(first, Property)
    assert first.titulo == "Exemplo"
    assert first.id_anuncio == "123"
    assert first.tipo_imovel == "Apartment"
    assert first.preco == Decimal("450000")
    assert first.moeda == "BRL"
    assert first.area_m2 == Decimal("85")
    assert (first.quartos, first.banheiros, first.vagas) == (2, 1, 1)
    assert first.localizacao == "Centro, Porto Alegre"
    assert first.cidade is None and first.bairro is None and first.estado is None
    assert first.url == "https://example.com/imovel/123"
    assert first.fonte == "VivaReal"
    assert second.id_anuncio == "456"
    assert second.titulo == "Outro imóvel fictício"
    assert second.preco == Decimal("200000")
    assert second.area_m2 == Decimal("50")
    assert second.url == "https://example.com/imovel/456"
    assert second.quartos is None and second.finalidade is None


def test_json_ld_nested_and_normalization() -> None:
    html = (Path(__file__).parent / "fixtures" / "vivareal_exemplo.html").read_text(encoding="utf-8")
    first, second = parse_properties(html, BASE_URL)
    assert first.titulo == "Apartamento no Centro"
    assert first.preco == Decimal("450000")
    assert first.moeda == "BRL"
    assert first.area_m2 == Decimal("85.5")
    assert (first.cidade, first.bairro, first.endereco) == ("Porto Alegre", "Centro", "Rua Exemplo, 100")
    assert first.url == "https://example.com/imovel/apartamento-12345/"
    assert second.id_anuncio == "67890" and second.preco is None and second.fonte is None


def test_search_results_only_and_relative_url_without_base() -> None:
    payload = {"@type": "SearchResults", "items": [
        {"listingId": "123", "name": "  Apto  ", "price": "R$ 1.234,50",
         "floorSize": "75,5", "numberOfBedrooms": "2", "url": "/imovel/123"}
    ]}
    html = f'<script type="application/json">{json.dumps(payload)}</script>'
    result = parse_properties(html)
    assert len(result) == 1
    prop = result[0]
    assert (prop.titulo, prop.id_anuncio, prop.url) == ("Apto", "123", "/imovel/123")
    assert (prop.preco, prop.area_m2, prop.quartos) == (Decimal("1234.50"), Decimal("75.5"), 2)
    assert prop.moeda == "BRL"


def test_html_cards_only() -> None:
    html = '<article class="property-card"><h2> Apto </h2><span>R$ 2.000</span><span>30 m²</span><a href="/imovel/1">Ver</a></article>'
    result = parse_properties(html)
    assert len(result) == 1
    assert (result[0].preco, result[0].area_m2, result[0].moeda) == (Decimal("2000"), Decimal("30"), "BRL")
    assert result[0].id_anuncio is None


def test_deduplication_by_identifier_and_content() -> None:
    json_ld = [{"@type": "Apartment", "identifier": "1", "name": "Primeiro"},
               {"@type": "Apartment", "identifier": "1", "name": "Ignorado"},
               {"@type": "Apartment", "name": "Sem id", "offers": {"price": 100}},
               {"@type": "Apartment", "offers": {"price": 100}, "name": "Sem id"}]
    html = f'<script type="application/ld+json">{json.dumps(json_ld)}</script>'
    result = parse_properties(html)
    assert len(result) == 2
    assert result[0].titulo == "Primeiro"


def test_distinct_identifiers_are_not_merged_even_with_identical_data() -> None:
    payload = {"@type": "SearchResults", "items": [
        {"listingId": "1", "name": "Mesmo"},
        {"listingId": "2", "name": "Mesmo"},
    ]}
    html = f'<script type="application/json">{json.dumps(payload)}</script>'
    assert len(parse_properties(html)) == 2


def test_bridge_record_merges_previously_separate_candidates() -> None:
    html = (
        '<script type="application/ld+json">[{"@type":"Apartment","identifier":"abc","name":"Primeiro"},'
        '{"@type":"Apartment","url":"/imovel/123","offers":{"price":9}}]</script>'
        '<script type="application/json">{"@type":"SearchResults",'
        '"items":[{"listingId":"abc","url":"/imovel/123","numberOfBedrooms":2}]}</script>'
    )
    result = parse_properties(html)
    assert len(result) == 1
    assert (result[0].id_anuncio, result[0].preco, result[0].quartos) == ("abc", Decimal("9"), 2)


def test_structured_decimal_precision_is_preserved() -> None:
    html = ('<script type="application/ld+json">{"@type":"Apartment",'
            '"offers":{"price":123456789012345.67}}</script>')
    assert parse_properties(html)[0].preco == Decimal("123456789012345.67")


def test_ambiguous_bridge_with_conflicting_identifiers_fails() -> None:
    html = (
        '<script type="application/ld+json">[{"@type":"Apartment","identifier":"1","url":"/imovel/a"},'
        '{"@type":"Apartment","identifier":"2","url":"/imovel/a"}]</script>'
        '<article class="property-card"><h2>Imóvel</h2><a href="/imovel/a">Ver</a></article>'
    )
    with pytest.raises(PropertyParseError, match="Identificadores conflitantes"):
        parse_properties(html)


def test_no_properties_returns_empty_list() -> None:
    assert parse_properties("<html><body>Sem resultados</body></html>") == []


@pytest.mark.parametrize("html", ["", "  ", "texto sem elementos"])
def test_invalid_html_fails(html: str) -> None:
    with pytest.raises(PropertyParseError, match="HTML"):
        parse_properties(html)


@pytest.mark.parametrize("html", [
    '<script type="application/ld+json">{invalid}</script>',
    '<script type="application/json">{invalid}</script>',
    '<script>window.__STATE__ = {invalid};</script>',
    '<script type="application/json">{"@type":"SearchResults","items":"inválido"}</script>',
    '<script type="application/json">{"@type":"SearchResults","items":[{}]}</script>',
])
def test_invalid_structured_data_fails(html: str) -> None:
    with pytest.raises(PropertyParseError):
        parse_properties(html)


def test_invalid_url_and_unit_fail() -> None:
    html = '<script type="application/ld+json">{"@type":"Apartment","url":"javascript:alert(1)"}</script>'
    with pytest.raises(PropertyParseError):
        parse_properties(html)
    html = '<script type="application/ld+json">{"@type":"Apartment","floorSize":{"value":10,"unitCode":"FTK"}}</script>'
    with pytest.raises(PropertyParseError):
        parse_properties(html)


def test_synthetic_json_ld_and_cards_combine_without_duplicates() -> None:
    apartments = [
        {
            "@type": "Apartment", "@id": identifier, "name": f"Imóvel {identifier}",
            "url": url, "address": {"streetAddress": address, "addressLocality": "Cidade Exemplo",
                                     "addressRegion": "RS"},
            "numberOfBedrooms": 2, "numberOfBathroomsTotal": 1,
            "floorSize": {"value": 75, "unitCode": "M2"},
        }
        for identifier, url, address in [
            ("1", "https://example.com/imovel/primeiro-id-1/", "Rua Exemplo, 10"),
            ("2", "https://example.com/imoveis-lancamentos/segundo-id-2/", "Outra rua"),
        ]
    ]
    products = [
        {"@type": "Product", "@id": identifier, "offers": {"price": 500000, "priceCurrency": "BRL"}}
        for identifier in ("2", "1")
    ]
    item_list = {"@type": "ItemList", "itemListElement": [{"item": product} for product in products]}
    html = (
        _json_ld_script(item_list) + _json_ld_script(apartments)
        + _real_style_card("/imovel/primeiro-id-1/", parking="2")
        + _real_style_card("#sem-link", parking="1", address="Outra rua")
    )
    properties = parse_properties(html, BASE_URL, source="VivaReal")
    assert [prop.id_anuncio for prop in properties] == ["1", "2"]
    assert [prop.vagas for prop in properties] == [2, 1]
    assert [prop.url for prop in properties] == [apartment["url"] for apartment in apartments]
    assert all(
        prop.preco == Decimal("500000") and prop.area_m2 == Decimal("75")
        and prop.quartos == 2 and prop.banheiros == 1 and prop.moeda == "BRL"
        and prop.cidade == "Cidade Exemplo" and prop.estado == "RS" and prop.fonte == "VivaReal"
        for prop in properties
    )


def _real_style_card(href: str, parking: str = "2", address: str = "Rua Exemplo, 10") -> str:
    return (
        f'<li data-cy="rp-property-cd"><a class="olx-core-card" href="{href}"></a>'
        f'<span data-cy="rp-cardProperty-street-txt">{address}</span>'
        '<span data-cy="rp-cardProperty-price-txt">R$ 500.000 Cond. R$ 900</span>'
        '<span data-cy="rp-cardProperty-propertyArea-txt">Tamanho do imóvel 70 - 80 m²</span>'
        '<span data-cy="rp-cardProperty-bedroomQuantity-txt">Quantidade de quartos 2</span>'
        '<span data-cy="rp-cardProperty-bathroomQuantity-txt">Quantidade de banheiros 1 - 2</span>'
        f'<span data-cy="rp-cardProperty-parkingSpacesQuantity-txt">Quantidade de vagas de garagem {parking}</span>'
        '</li>'
    )


def _json_ld_script(payload: object) -> str:
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


def test_json_ld_products_match_apartments_by_id_even_when_reordered() -> None:
    apartments = [
        {"@type": "Apartment", "@id": "1", "identifier": "outro-1", "name": "Um", "floorSize": {"value": 70, "unitCode": "M2"}},
        {"@type": "Apartment", "@id": "2", "identifier": "outro-2", "name": "Dois", "floorSize": {"value": 80, "unitCode": "M2"}},
    ]
    products = [
        {"@type": "Product", "@id": "2", "offers": {"price": 200, "priceCurrency": "BRL"}},
        {"@type": "Product", "@id": "1", "offers": {"price": 100, "priceCurrency": "BRL"}},
        {"@type": "Product", "@id": "outro", "offers": {"price": 999}},
    ]
    properties = parse_properties(_json_ld_script(products) + _json_ld_script(apartments))
    assert [(p.id_anuncio, p.titulo, p.preco, p.moeda, p.area_m2) for p in properties] == [
        ("1", "Um", Decimal("100"), "BRL", Decimal("70")),
        ("2", "Dois", Decimal("200"), "BRL", Decimal("80")),
    ]


def test_parking_matches_fragment_only_when_unambiguous_and_within_ranges() -> None:
    apartment = {
        "@type": "Apartment", "@id": "1", "name": "Apartamento",
        "address": {"streetAddress": "Rua Exemplo, 10"}, "numberOfBedrooms": 2,
        "numberOfBathroomsTotal": 1, "floorSize": {"value": 75, "unitCode": "M2"},
        "url": "https://example.com/imoveis-lancamentos/exemplo-id-1/",
    }
    product = {"@type": "Product", "@id": "1", "offers": {"price": 500000, "priceCurrency": "BRL"}}
    structured = _json_ld_script([product, apartment])
    prop, = parse_properties(structured + _real_style_card("#sem-link"))
    assert prop.vagas == 2
    assert prop.url == apartment["url"]

    prop, = parse_properties(structured + _real_style_card("#sem-link", parking="1 - 2"))
    assert prop.vagas is None
    prop, = parse_properties(structured + _real_style_card("#sem-link") * 2)
    assert prop.vagas is None
    prop, = parse_properties(structured + _real_style_card("#sem-link", address="Outra rua"))
    assert prop.vagas is None

    second = dict(apartment, **{"@id": "2", "url": "https://example.com/imovel/outro-id-2/"})
    second_product = dict(product, **{"@id": "2"})
    properties = parse_properties(_json_ld_script([apartment, product, second, second_product]) + _real_style_card("#sem-link"))
    assert len(properties) == 2
    assert all(prop.vagas is None for prop in properties)


def test_parking_for_identified_card_does_not_create_new_property_or_replace_url() -> None:
    apartment = {"@type": "Apartment", "@id": "1", "url": "https://example.com/imoveis-lancamentos/exemplo-id-1/"}
    structured = _json_ld_script(apartment)
    html = structured + _real_style_card("/imoveis-lancamentos/exemplo-id-1/?development_unit_id=9")
    prop, = parse_properties(html, BASE_URL)
    assert prop.vagas == 2
    assert prop.url == apartment["url"]
    prop, = parse_properties(structured + _real_style_card("/imovel/outro-id-9/"), BASE_URL)
    assert prop.vagas is None


def test_url_only_card_match_does_not_invent_an_identifier() -> None:
    url = "https://example.com/imovel/exemplo-id-1/"
    html = _json_ld_script({"@type": "Apartment", "url": url}) + _real_style_card(url)
    prop, = parse_properties(html)
    assert prop.id_anuncio is None
    assert prop.url == url
    assert prop.vagas == 2
