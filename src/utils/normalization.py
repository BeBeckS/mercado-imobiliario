import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit


def text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ValueError(f"Texto inválido: {value!r}")
    result = " ".join(str(value).split())
    return result or None


def number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Valor numérico inválido.")
    if isinstance(value, (int, float, Decimal)):
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"Valor numérico inválido: {value!r}") from exc
    elif isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", " ")
        cleaned = re.sub(r"^(?:R\$\s*)", "", cleaned)
        cleaned = re.sub(r"\s*(?:m²|m2)\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace(" ", "")
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", cleaned):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d+(?:,\d+)", cleaned):
            cleaned = cleaned.replace(",", ".")
        elif not re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
            raise ValueError(f"Valor numérico inválido: {value!r}")
        try:
            result = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"Valor numérico inválido: {value!r}") from exc
    else:
        raise ValueError(f"Valor numérico inválido: {value!r}")
    if not result.is_finite() or result < 0:
        raise ValueError(f"Valor numérico deve ser finito e não negativo: {value!r}")
    canonical = format(result, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return Decimal(canonical)


def integer(value: Any) -> int | None:
    result = number(value)
    if result is None:
        return None
    if result != result.to_integral_value():
        raise ValueError(f"Quantidade deve ser inteira: {value!r}")
    return int(result)


def absolute_url(value: Any, base_url: str) -> str | None:
    value = text(value)
    if value is None:
        return None
    result = urljoin(base_url, value)
    parsed = urlsplit(result)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"URL inválida: {value!r}")
    return result
