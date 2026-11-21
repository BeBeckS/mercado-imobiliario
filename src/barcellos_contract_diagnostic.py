"""Diagnóstico offline do contrato JSON, sem imprimir valores de imóveis."""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from src.collectors.diagnostic import SENSITIVE_NAME
from src.utils.normalization import integer, number

logger = logging.getLogger(__name__)

NUMERIC_FIELDS: dict[str, Callable[[Any], Decimal | int | None]] = {
    "valor": number,
    "valor_formatado": number,
    "valor_condominio": number,
    "valor_iptu": number,
    "area": number,
    "dormitorios": integer,
    "suites": integer,
    "banheiros": integer,
    "garagens": integer,
}


def _type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (float, Decimal)):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "outro"


def _safe_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,80}", key)) and not SENSITIVE_NAME.search(key)


def _items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "SUCESSO":
        raise ValueError("Resposta JSON sem status de sucesso.")
    items = payload.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Resposta JSON sem lista válida de imóveis em items.")
    return items


def _field_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    keys = sorted({key for item in items for key in item if isinstance(key, str) and _safe_key(key)})
    result: dict[str, Any] = {}
    for key in keys:
        values = [item[key] for item in items if key in item]
        result[key] = {
            "tipos": dict(sorted(Counter(_type(value) for value in values).items())),
            "ausentes": len(items) - len(values),
            "nulos": sum(value is None for value in values),
            "vazios": sum(isinstance(value, str) and not value.strip() for value in values),
        }
    return result


def _numeric_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for key, converter in NUMERIC_FIELDS.items():
        counts = {"validos": 0, "ausentes": 0, "nulos": 0, "vazios": 0, "invalidos": 0, "zeros": 0}
        for item in items:
            if key not in item:
                counts["ausentes"] += 1
            elif item[key] is None:
                counts["nulos"] += 1
            elif isinstance(item[key], str) and not item[key].strip():
                counts["vazios"] += 1
            else:
                try:
                    value = converter(item[key])
                except ValueError:
                    counts["invalidos"] += 1
                else:
                    counts["validos" if value is not None else "invalidos"] += 1
                    if value is not None and value == 0:
                        counts["zeros"] += 1
        report[key] = counts
    return report


def _identifiers(items: list[dict[str, Any]], field: str) -> list[str]:
    values: list[str] = []
    for item in items:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif field == "code" and isinstance(value, int) and not isinstance(value, bool):
            values.append(str(value))
    return values


def _urls(items: list[dict[str, Any]]) -> dict[str, int]:
    report = {
        "relativas": 0, "absolutas_http": 0, "outras_ou_invalidas": 0,
        "caminho_vendas": 0, "slug_prefixo_codigo": 0,
    }
    for item in items:
        value = item.get("url")
        if not isinstance(value, str) or not value.strip():
            report["outras_ou_invalidas"] += 1
            continue
        try:
            parsed = urlsplit(value.strip())
            if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
                report["absolutas_http"] += 1
            elif not parsed.scheme and not parsed.netloc and parsed.path.startswith("/"):
                report["relativas"] += 1
            else:
                report["outras_ou_invalidas"] += 1
            if parsed.path.startswith("/vendas/"):
                report["caminho_vendas"] += 1
            code = item.get("code")
            if isinstance(code, (str, int)) and not isinstance(code, bool) and str(code).isdigit():
                prefix = "v" + str(code).zfill(5) + "-"
                if parsed.path.rsplit("/", 1)[-1].lower().startswith(prefix):
                    report["slug_prefixo_codigo"] += 1
        except ValueError:
            report["outras_ou_invalidas"] += 1
    return report


def _filter_shape(payload: dict[str, Any]) -> dict[str, Any] | None:
    filters = payload.get("filtros")
    if not isinstance(filters, dict):
        return None
    result: dict[str, Any] = {}
    for key, value in filters.items():
        if not isinstance(key, str) or not _safe_key(key):
            continue
        detail: dict[str, Any] = {"tipo": _type(value)}
        if isinstance(value, list):
            detail["tamanho"] = len(value)
            detail["tipos_itens"] = dict(sorted(Counter(_type(item) for item in value).items()))
            detail["chaves_objetos"] = sorted({
                name for item in value if isinstance(item, dict)
                for name in item if isinstance(name, str) and _safe_key(name)
            })
        elif isinstance(value, dict):
            detail["chaves"] = sorted(name for name in value if isinstance(name, str) and _safe_key(name))
        result[key] = detail
    return result


def _pagination(payload: dict[str, Any]) -> dict[str, Any]:
    page = payload.get("pagina")
    more = payload.get("tem_mais")
    if page is not None and (not isinstance(page, int) or isinstance(page, bool)):
        raise ValueError("Campo pagina inválido.")
    if more is not None and not isinstance(more, bool):
        raise ValueError("Campo tem_mais inválido.")
    return {"pagina": page, "tem_mais": more}


def diagnose_contract(payload: Any, second_payload: Any | None = None) -> dict[str, Any]:
    items = _items(payload)
    codes = _identifiers(items, "code")
    urls = _identifiers(items, "url")
    result: dict[str, Any] = {
        "raiz": {key: _type(value) for key, value in payload.items() if isinstance(key, str) and _safe_key(key)},
        "filtros": _filter_shape(payload),
        "lista": {"chave": "items", "quantidade": len(items), "campos": _field_report(items)},
        "conversoes": _numeric_report(items),
        "identidade": {
            "codigos_preenchidos": len(codes),
            "codigos_distintos": len(set(codes)),
            "codigos_numericos": sum(code.isdigit() for code in codes),
            "urls_preenchidas": len(urls),
            "urls_distintas": len(set(urls)),
            "outros_campos_id": sorted({
                key for item in items for key in item
                if isinstance(key, str) and _safe_key(key)
                and re.search(r"(^id$|^id_|_id$|uuid|guid)", key, re.IGNORECASE)
            }),
        },
        "urls": _urls(items),
        "valor_formatado_com_prefixo_rs": sum(
            isinstance(item.get("valor_formatado"), str)
            and bool(re.match(r"^R\$\s*", item["valor_formatado"]))
            for item in items
        ),
        "valor_formatado_padrao_brl": sum(
            isinstance(item.get("valor_formatado"), str)
            and bool(re.fullmatch(r"R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{2})?", item["valor_formatado"]))
            for item in items
        ),
        "sob_consulta_com_valor_nao_zero": sum(
            item.get("sob_consulta") is True
            and isinstance(item.get("valor"), (int, float, Decimal))
            and not isinstance(item.get("valor"), bool)
            and item["valor"] != 0
            for item in items
        ),
        "paginacao": _pagination(payload),
    }
    if second_payload is not None:
        second = _items(second_payload)
        codes2 = _identifiers(second, "code")
        urls2 = _identifiers(second, "url")
        second_pagination = _pagination(second_payload)
        result["paginacao"].update({
            "pagina_seguinte": second_pagination["pagina"],
            "tem_mais_seguinte": second_pagination["tem_mais"],
            "itens_seguinte": len(second),
            "codigos_distintos_seguinte": len(set(codes2)),
            "urls_distintas_seguinte": len(set(urls2)),
            "codigos_em_ambas": len(set(codes) & set(codes2)),
            "urls_em_ambas": len(set(urls) & set(urls2)),
        })
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnosticar respostas JSON locais, sem acessar a rede.")
    parser.add_argument("--json-file", required=True, type=Path)
    parser.add_argument("--second-json-file", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        payload = json.loads(args.json_file.read_text(encoding="utf-8"), parse_float=Decimal)
        second = None
        if args.second_json_file:
            second = json.loads(args.second_json_file.read_text(encoding="utf-8"), parse_float=Decimal)
        report = diagnose_contract(payload, second)
    except (OSError, UnicodeError):
        logger.error("Diagnóstico não realizado: erro de leitura.")
        return 1
    except (ValueError, RecursionError):
        logger.error("Diagnóstico não realizado: resposta JSON inválida ou sem imóveis válidos.")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
