import re
from urllib.parse import urlsplit

from src.models.property import Property
from src.utils.hashing import record_hash


def _identity(prop: Property) -> str | None:
    if prop.id_anuncio:
        return prop.id_anuncio
    if prop.url:
        match = re.fullmatch(r"/imovel/([^/]+)/?", urlsplit(prop.url).path)
        if match:
            return match.group(1)
    return None


def same_property(left: Property, right: Property) -> bool:
    if (left.fonte or "").strip().casefold() != (right.fonte or "").strip().casefold():
        return False
    if left.url and right.url:
        left_host = urlsplit(left.url).hostname
        right_host = urlsplit(right.url).hostname
        if left_host and right_host and left_host != right_host:
            return False
    if left.id_anuncio and right.id_anuncio:
        return left.id_anuncio == right.id_anuncio
    if left.url and right.url and left.url == right.url:
        return True
    left_id, right_id = _identity(left), _identity(right)
    if left_id and right_id:
        return left_id == right_id
    if left_id or right_id or left.url or right.url:
        return False
    left_data = left.model_dump(exclude_none=True, exclude={"fonte"})
    right_data = right.model_dump(exclude_none=True, exclude={"fonte"})
    return len(left_data) >= 2 and record_hash(left_data) == record_hash(right_data)


def merge_property(
    properties: list[Property], incoming: Property, *, prefer_incoming: bool = False
) -> None:
    matching: set[int] = set()
    combined = incoming
    while True:
        new_matches = {
            index for index, current in enumerate(properties)
            if index not in matching and same_property(current, combined)
        }
        if not new_matches:
            break
        matching.update(new_matches)
        identifiers = {
            prop.id_anuncio
            for prop in [properties[index] for index in matching] + [incoming]
            if prop.id_anuncio
        }
        if len(identifiers) > 1:
            raise ValueError("Identificadores conflitantes para o mesmo imóvel.")
        values = properties[min(matching)].model_dump()
        for prop in [properties[index] for index in sorted(matching)[1:]]:
            for key, value in prop.model_dump().items():
                if values[key] is None and value is not None:
                    values[key] = value
        for key, value in incoming.model_dump().items():
            if value is not None and (prefer_incoming or values[key] is None):
                values[key] = value
        combined = Property.model_validate(values)
    if matching:
        first = min(matching)
        properties[first] = combined
        for index in sorted(matching - {first}, reverse=True):
            del properties[index]
    else:
        properties.append(incoming)
