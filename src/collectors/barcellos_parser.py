"""Validação e mapeamento do JSON da Barcellos para Property."""

from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from src.config.settings import validate_source_url
from src.models.property import Property
from src.utils.normalization import absolute_url, number, text


def _text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Campo {field} deve ser uma string.")
    return text(value)


def _numeric(value: Any, field: str) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"Campo {field} deve ser numérico.")
    result = number(value)
    if result is None:
        raise ValueError(f"Campo {field} inválido.")
    return result


def _quantity(value: Any, field: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Campo {field} deve ser inteiro.")
    return value


def _property_url(value: Any, base_url: str) -> str | None:
    raw = _text(value, "url")
    if raw is None:
        return None
    if not urlsplit(raw).path:
        raise ValueError("URL do imóvel sem caminho.")
    url = absolute_url(raw, base_url)
    if url is None:
        raise ValueError("URL do imóvel inválida.")
    validate_source_url(url)
    parsed, base = urlsplit(url), urlsplit(base_url)
    if (parsed.scheme, parsed.hostname, parsed.port) != (base.scheme, base.hostname, base.port):
        raise ValueError("URL do imóvel fora da origem configurada.")
    return url


def parse_barcellos_item(data: dict[str, Any], source_url: str) -> Property:
    identifier = _text(data.get("code"), "code")
    if identifier is None:
        raise ValueError("Imóvel Barcellos sem code.")
    consult = data.get("sob_consulta")
    if consult is not None and consult != "" and not isinstance(consult, bool):
        raise ValueError("Campo sob_consulta deve ser booleano.")
    formatted = _text(data.get("valor_formatado"), "valor_formatado")
    return Property(
        id_anuncio=identifier,
        titulo=_text(data.get("titulo"), "titulo"),
        tipo_imovel=_text(data.get("tipo"), "tipo"),
        finalidade=_text(data.get("finalidade"), "finalidade"),
        bairro=_text(data.get("bairro"), "bairro"),
        cidade=_text(data.get("cidade"), "cidade"),
        estado=_text(data.get("estado"), "estado"),
        area_m2=_numeric(data.get("area"), "area"),
        quartos=_quantity(data.get("dormitorios"), "dormitorios"),
        banheiros=_quantity(data.get("banheiros"), "banheiros"),
        vagas=_quantity(data.get("garagens"), "garagens"),
        preco=_numeric(data.get("valor"), "valor"),
        moeda="BRL" if formatted and formatted.startswith("R$") else None,
        url=_property_url(data.get("url"), source_url),
        fonte="Barcellos",
    )


def parse_barcellos_page(
    payload: Any, source_url: str, expected_page: int
) -> tuple[list[Property], bool, int]:
    if not isinstance(payload, dict) or payload.get("status") != "SUCESSO":
        raise ValueError("Resposta Barcellos sem status de sucesso.")
    if payload.get("finalidade") != "vendas":
        raise ValueError("Resposta Barcellos com finalidade diferente de vendas.")
    page = payload.get("pagina")
    if isinstance(page, bool) or not isinstance(page, int) or page != expected_page:
        raise ValueError("Página Barcellos inválida ou não avançou.")
    has_more = payload.get("tem_mais")
    if not isinstance(has_more, bool):
        raise ValueError("Campo tem_mais da Barcellos deve ser booleano.")
    total = payload.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("Campo total da Barcellos deve ser inteiro não negativo.")
    truncated = payload.get("truncado")
    if "truncado" in payload and not isinstance(truncated, bool):
        raise ValueError("Campo truncado da Barcellos deve ser booleano.")
    if truncated is True:
        raise ValueError("Resposta Barcellos truncada; coleta não pode ser consolidada.")
    items = payload.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Lista items da Barcellos inválida.")
    count = payload.get("count")
    if "count" in payload and (
        isinstance(count, bool) or not isinstance(count, int) or count != len(items)
    ):
        raise ValueError("Campo count da Barcellos incompatível com items.")
    return [parse_barcellos_item(item, source_url) for item in items], has_more, total
