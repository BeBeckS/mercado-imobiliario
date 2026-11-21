import json
from datetime import datetime
from typing import Any, Iterator

from bs4 import BeautifulSoup
from pydantic import ValidationError

from src.collectors.http_client import CollectionError
from src.models.listing import Listing
from src.utils.hashing import record_hash
from src.utils.normalization import absolute_url, integer, number, text


PROPERTY_TYPES = {"Apartment", "House", "SingleFamilyResidence", "Residence"}


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in value:
                yield from _objects(value[key])


def _property_type(value: Any) -> str | None:
    types = value if isinstance(value, list) else [value]
    return next((item for item in types if isinstance(item, str) and item in PROPERTY_TYPES), None)


def _listing(data: dict[str, Any], base_url: str, collected_at: datetime) -> Listing:
    address = data.get("address") or {}
    if isinstance(address, str):
        address = {"streetAddress": address}
    if not isinstance(address, dict):
        raise ValueError("Endereço JSON-LD inválido.")
    offers = data.get("offers") or {}
    if isinstance(offers, list):
        if len(offers) != 1:
            raise ValueError("Quantidade de ofertas ambígua.")
        offers = offers[0]
    if not isinstance(offers, dict):
        raise ValueError("Oferta JSON-LD inválida.")
    currency = offers.get("priceCurrency")
    if currency is not None and currency != "BRL":
        raise ValueError(f"Moeda não suportada: {currency!r}")
    area = data.get("floorSize")
    if isinstance(area, dict):
        unit = area.get("unitCode") or area.get("unitText")
        if unit is not None and unit not in ("MTK", "m²", "m2"):
            raise ValueError(f"Unidade de área não suportada: {unit!r}")
        area = area.get("value")
    identifier = data.get("identifier")
    if isinstance(identifier, dict):
        identifier = identifier.get("value")
    values = {
        "id_anuncio": text(identifier),
        "titulo": text(data.get("name")),
        "preco": number(offers.get("price")),
        "area_m2": number(area),
        "quartos": integer(data.get("numberOfBedrooms")),
        "banheiros": integer(data.get("numberOfBathroomsTotal")),
        "vagas": integer(data.get("numberOfParkingSpaces")),
        "tipo_imovel": _property_type(data.get("@type")),
        "bairro": text(address.get("addressNeighborhood")),
        "cidade": text(address.get("addressLocality")),
        "endereco": text(address.get("streetAddress")),
        "url": absolute_url(data.get("url"), base_url),
        "fonte": "VivaReal",
        "data_coleta": collected_at,
    }
    listing = Listing(**values, hash_registro="0" * 64)
    return listing.model_copy(update={"hash_registro": record_hash(listing.model_dump(mode="json"))})


def parse_listings(html: str, base_url: str, collected_at: datetime) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", type="application/ld+json")
    listings: list[Listing] = []
    seen: set[str] = set()
    try:
        for script in scripts:
            payload = json.loads(script.string or script.get_text())
            for item in _objects(payload):
                if _property_type(item.get("@type")):
                    listing = _listing(item, base_url, collected_at)
                    if listing.hash_registro not in seen:
                        seen.add(listing.hash_registro)
                        listings.append(listing)
    except (ValueError, ValidationError, RecursionError) as exc:
        raise CollectionError(f"Não foi possível interpretar os anúncios JSON-LD: {exc}") from exc
    if not listings:
        raise CollectionError(
            "Nenhum anúncio reconhecido em JSON-LD; página vazia, restrita ou estrutura não suportada."
        )
    return listings
