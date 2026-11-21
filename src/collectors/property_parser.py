"""Parser independente de HTTP para as estruturas das fixtures locais."""

import json
import re
from decimal import Decimal
from typing import Any, Iterator
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag
from src.models.property import Property
from src.utils.normalization import absolute_url, integer, number, text
from src.utils.property_identity import merge_property, same_property


class PropertyParseError(ValueError):
    """HTML ou dados estruturados não puderam ser interpretados."""


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


def _url(value: Any, base_url: str | None) -> str | None:
    value = text(value)
    if value is None:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("URL do anúncio inválida.")
    if base_url:
        return absolute_url(value, base_url)
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise ValueError("URL do anúncio deve ser HTTP(S) ou relativa.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL do anúncio não deve conter credenciais.")
    if parsed.scheme and not parsed.hostname:
        raise ValueError("URL do anúncio inválida.")
    return value


def _area(value: Any) -> Any:
    if isinstance(value, dict):
        unit = value.get("unitCode") or value.get("unitText")
        if unit is not None and unit not in ("MTK", "m²", "m2", "M2"):
            raise ValueError("Unidade de área não suportada.")
        return value.get("value")
    return value


def _structured(
    data: dict[str, Any], base_url: str | None, source: str | None, *, json_ld: bool = False
) -> Property:
    address = data.get("address")
    if address is None:
        address = {}
    elif isinstance(address, str):
        address = {"streetAddress": address}
    elif not isinstance(address, dict):
        raise ValueError("Endereço estruturado inválido.")
    offers = data.get("offers")
    if offers is None:
        offers = {}
    elif isinstance(offers, list):
        if len(offers) != 1:
            raise ValueError("Quantidade de ofertas ambígua.")
        offers = offers[0]
    if not isinstance(offers, dict):
        raise ValueError("Oferta estruturada inválida.")
    identifier = data.get("@id") if json_ld else None
    if identifier is None:
        identifier = data.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    if identifier is None:
        identifier = data.get("listingId")
    additional_type = data.get("additionalType")
    property_type = "Apartment" if (
        _has_type(data, "Apartment")
        or additional_type == "https://schema.org/Apartment"
    ) else None
    raw_price = offers.get("price") if "price" in offers else data.get("price")
    currency = text(offers.get("priceCurrency"))
    if currency is None and isinstance(raw_price, str) and raw_price.strip().startswith("R$"):
        currency = "BRL"
    return Property(
        titulo=text(data.get("name")),
        preco=number(raw_price),
        moeda=currency,
        tipo_imovel=property_type,
        endereco=text(address.get("streetAddress")),
        cidade=text(address.get("addressLocality")),
        bairro=text(address.get("addressNeighborhood")),
        estado=text(address.get("addressRegion")),
        area_m2=number(_area(data.get("floorSize"))),
        quartos=integer(data.get("numberOfBedrooms")),
        banheiros=integer(data.get("numberOfBathroomsTotal")),
        vagas=integer(data.get("numberOfParkingSpaces")),
        url=_url(data.get("url"), base_url),
        id_anuncio=text(identifier),
        fonte=source,
    )


def _has_type(data: dict[str, Any], name: str) -> bool:
    value = data.get("@type")
    return value == name or (isinstance(value, list) and name in value)


def _card(card: Tag, base_url: str | None, source: str | None) -> Property:
    heading = card.select_one("h2")
    price = card.select_one(".listing-price")
    area = card.select_one(".listing-area")
    for span in card.find_all("span"):
        value = span.get_text(" ", strip=True)
        if price is None and re.fullmatch(r"R\$\s*[\d.,]+", value):
            price = span
        if area is None and re.fullmatch(r"[\d.,]+\s*m[²2]", value, re.IGNORECASE):
            area = span
    location = card.select_one(".listing-location")
    link = card.select_one("a[href]")
    body = card.get_text(" ", strip=True)

    def quantity(pattern: str) -> int | None:
        match = re.search(pattern, body, re.IGNORECASE)
        return integer(match.group(1)) if match else None

    price_text = price.get_text(" ", strip=True) if price else None
    return Property(
        titulo=text(heading.get_text(" ", strip=True)) if heading else None,
        preco=number(price_text),
        moeda="BRL" if price_text and price_text.strip().startswith("R$") else None,
        localizacao=text(location.get_text(" ", strip=True)) if location else None,
        area_m2=number(area.get_text(" ", strip=True)) if area else None,
        quartos=quantity(r"\b(\d+)\s+quartos?\b"),
        banheiros=quantity(r"\b(\d+)\s+banheiros?\b"),
        vagas=quantity(r"\b(\d+)\s+vagas?\b"),
        url=_url(link.get("href"), base_url) if link else None,
        id_anuncio=text(card.get("data-listing-id")),
        fonte=source,
    )


def _card_text(card: Tag, field: str) -> str | None:
    element = card.select_one(f'[data-cy="{field}"]')
    return text(element.get_text(" ", strip=True)) if element else None


def _card_signature(card: Tag) -> tuple[Property, Decimal, Decimal, int, int] | None:
    address = _card_text(card, "rp-cardProperty-street-txt")
    price = _card_text(card, "rp-cardProperty-price-txt") or ""
    area = _card_text(card, "rp-cardProperty-propertyArea-txt") or ""
    rooms = _card_text(card, "rp-cardProperty-bedroomQuantity-txt") or ""
    bathrooms = _card_text(card, "rp-cardProperty-bathroomQuantity-txt") or ""
    price_match = re.search(r"R\$\s*[\d.,]+", price)
    area_match = re.fullmatch(
        r"Tamanho do imóvel\s+([\d.,]+)(?:\s*-\s*([\d.,]+))?\s*m[²2]", area, re.IGNORECASE
    )
    rooms_match = re.fullmatch(r"Quantidade de quartos\s+(\d+)", rooms, re.IGNORECASE)
    bathrooms_match = re.fullmatch(
        r"Quantidade de banheiros\s+(\d+)(?:\s*-\s*(\d+))?", bathrooms, re.IGNORECASE
    )
    if not (address and price_match and area_match and rooms_match and bathrooms_match):
        return None
    area_min = number(area_match.group(1))
    area_max = number(area_match.group(2) or area_match.group(1))
    bathrooms_min = int(bathrooms_match.group(1))
    bathrooms_max = int(bathrooms_match.group(2) or bathrooms_match.group(1))
    if area_min is None or area_max is None or area_min > area_max or bathrooms_min > bathrooms_max:
        return None
    return (
        Property(endereco=address, preco=number(price_match.group()), quartos=integer(rooms_match.group(1))),
        area_min, area_max, bathrooms_min, bathrooms_max,
    )


def _card_matches_property(signature: tuple[Property, Decimal, Decimal, int, int], prop: Property) -> bool:
    basic, area_min, area_max, bathrooms_min, bathrooms_max = signature
    if prop.area_m2 is None or prop.banheiros is None:
        return False
    candidate = Property(endereco=prop.endereco, preco=prop.preco, quartos=prop.quartos)
    return (
        same_property(basic, candidate)
        and area_min <= prop.area_m2 <= area_max
        and bathrooms_min <= prop.banheiros <= bathrooms_max
    )


def _merge_real_cards(properties: list[Property], soup: BeautifulSoup, base_url: str | None, source: str | None) -> None:
    cards = soup.select('li[data-cy="rp-property-cd"]')
    signatures = [signature for card in cards if (signature := _card_signature(card)) is not None]
    fragment_cards: list[tuple[tuple[Property, Decimal, Decimal, int, int], int]] = []
    for card in cards:
        parking = _card_text(card, "rp-cardProperty-parkingSpacesQuantity-txt")
        if parking is None:
            continue
        match = re.fullmatch(r"Quantidade de vagas de garagem\s+(\d+)", parking, re.IGNORECASE)
        if not match:
            continue
        spaces = int(match.group(1))
        link = card.select_one("a.olx-core-card[href]")
        href = text(link.get("href")) if link else None
        if href and not href.startswith("#"):
            url = _url(href, base_url)
            id_match = re.search(r"-id-(\d+)/?$", urlsplit(url or "").path)
            if id_match:
                incoming = Property(id_anuncio=id_match.group(1), url=url, vagas=spaces, fonte=source)
                matches = [prop for prop in properties if same_property(prop, incoming)]
                if len(matches) == 1:
                    matched = matches[0]
                    merge_property(properties, Property(
                        id_anuncio=matched.id_anuncio, url=matched.url, vagas=spaces, fonte=source
                    ))
            continue
        signature = _card_signature(card)
        if signature is not None:
            fragment_cards.append((signature, spaces))

    for signature, spaces in fragment_cards:
        matches = [prop for prop in properties if _card_matches_property(signature, prop)]
        if len(matches) != 1 or sum(_card_matches_property(other, matches[0]) for other in signatures) != 1:
            continue
        matched = matches[0]
        if not matched.id_anuncio and not matched.url:
            continue
        merge_property(properties, Property(id_anuncio=matched.id_anuncio, url=matched.url, vagas=spaces, fonte=source))


def parse_properties(
    html: str, base_url: str | None = None, source: str | None = None
) -> list[Property]:
    if not html.strip():
        raise PropertyParseError("HTML vazio.")
    soup = BeautifulSoup(html, "html.parser")
    if not soup.find(True):
        raise PropertyParseError("HTML inválido: nenhum elemento encontrado.")
    ld_records: list[Property] = []
    ld_products: list[dict[str, Any]] = []
    search_records: list[Property] = []
    try:
        for script in soup.find_all("script"):
            kind = str(script.get("type", "")).split(";", 1)[0].strip().lower()
            body = script.string or script.get_text()
            state_match = re.search(r"\bwindow\.__STATE__\s*=\s*", body) if not kind else None
            if kind not in {"application/ld+json", "application/json"} and not state_match:
                continue
            if state_match:
                payload, _ = json.JSONDecoder(parse_float=Decimal).raw_decode(body[state_match.end():])
            else:
                payload = json.loads(body, parse_float=Decimal)
            if kind == "application/ld+json":
                for item in _objects(payload):
                    if _has_type(item, "Apartment"):
                        ld_records.append(_structured(item, base_url, source, json_ld=True))
                    elif _has_type(item, "Product"):
                        ld_products.append(item)
            else:
                for item in _objects(payload):
                    if item.get("@type") == "SearchResults" or (state_match and item is payload):
                        entries = item.get("items")
                        if not isinstance(entries, list):
                            raise ValueError("Lista items do JSON estruturado inválida.")
                        for entry in entries:
                            if not isinstance(entry, dict):
                                raise ValueError("Item do JSON estruturado inválido.")
                            prop = _structured(entry, base_url, source)
                            if not prop.model_dump(exclude_none=True, exclude={"fonte"}):
                                raise ValueError("Item do JSON estruturado sem campos reconhecidos.")
                            search_records.append(prop)
        apartment_ids = {prop.id_anuncio for prop in ld_records if prop.id_anuncio}
        for item in ld_products:
            identifier = text(item.get("@id"))
            if item.get("additionalType") == "https://schema.org/Apartment" or (
                identifier is not None and identifier in apartment_ids
            ):
                ld_records.append(_structured(item, base_url, source, json_ld=True))
        properties: list[Property] = []
        for prop in ld_records + search_records:
            merge_property(properties, prop)
        for card in soup.select("article.property-card"):
            prop = _card(card, base_url, source)
            if prop.titulo or prop.url or prop.id_anuncio:
                merge_property(properties, prop)
        _merge_real_cards(properties, soup, base_url, source)
        return properties
    except (ValueError, RecursionError) as exc:
        raise PropertyParseError(f"Não foi possível interpretar os dados do imóvel: {exc}") from exc
