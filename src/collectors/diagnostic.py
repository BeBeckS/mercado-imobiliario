"""Diagnóstico heurístico de HTML, sem extrair anúncios."""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from src.collectors.http_client import CollectionError, HttpClient
from src.config.settings import Settings

logger = logging.getLogger(__name__)

SIGNALS = {
    "titulo": re.compile(r"\b(?:t[ií]tulo|title|name|nome)\b", re.I),
    "preco": re.compile(r"R\$\s*[\d.,]+|\b(?:pre[cç]o|price)\b", re.I),
    "localizacao": re.compile(r"\b(?:localiza[cç][aã]o|address|endere[cç]o|bairro|cidade|location)\b", re.I),
    "area": re.compile(r"\b(?:area|[aá]rea|floor\s*size)\b|\d[\d.,]*\s*m[²2]\b", re.I),
    "quartos": re.compile(r"\b(?:quartos?|dormit[oó]rios?|bedrooms?)\b", re.I),
    "banheiros": re.compile(r"\b(?:banheiros?|bathrooms?)\b", re.I),
    "vagas": re.compile(r"\b(?:vagas?|garagem|parking)\b", re.I),
    "url": re.compile(r"\b(?:url|href|link)\b", re.I),
    "identificador": re.compile(r"\b(?:id|identifier|listing.?id|property.?id|id.?anuncio)\b", re.I),
}
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,49}$")
RELEVANT_CLASS = re.compile(r"listing|property|imovel|im[oó]vel|card|anuncio|anúncio", re.I)
SENSITIVE_NAME = re.compile(r"token|secret|password|credential|cookie|session|auth|api.?key", re.I)
JSON_ASSIGNMENT = re.compile(r"(?:\b(?:window|self)\.[A-Za-z_$][\w$]*|\b(?:var|let|const)\s+[A-Za-z_$][\w$]*)\s*=\s*(?=[{\[])")
MAX_EXAMPLES = 5


def _signals(value: str) -> set[str]:
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value).replace("_", " ")
    return {name for name, pattern in SIGNALS.items() if pattern.search(value)}


def _selector(tag: Tag) -> str:
    classes = tag.get("class", [])
    safe_classes = [
        value for value in classes
        if isinstance(value, str)
        and SAFE_NAME.fullmatch(value)
        and not SENSITIVE_NAME.search(value)
        and (_signals(value) or RELEVANT_CLASS.search(value))
    ] if isinstance(classes, list) else []
    return tag.name + "".join(f".{value}" for value in safe_classes[:3])


def _inspect_json(payload: Any) -> tuple[set[str], set[str], int]:
    types: set[str] = set()
    fields: set[str] = set()
    candidates = 0
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            object_fields: set[str] = set()
            for key, child in value.items():
                if isinstance(key, str):
                    object_fields.update(_signals(key))
                    if key == "@type":
                        entries = child if isinstance(child, list) else [child]
                        types.update(
                            entry for entry in entries
                            if isinstance(entry, str) and SAFE_NAME.fullmatch(entry) and not SENSITIVE_NAME.search(entry)
                        )
                if isinstance(child, (dict, list)):
                    pending.append(child)
            fields.update(object_fields)
            if len(object_fields) >= 2 and object_fields & {"titulo", "url", "identificador"}:
                candidates += 1
    return types, fields, candidates


def diagnose_html(html: str) -> dict[str, Any]:
    if not html.strip():
        raise ValueError("HTML vazio; não há estrutura para diagnosticar.")
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")
    json_ld = 0
    embedded = 0
    invalid_json = 0
    json_ld_types: set[str] = set()
    embedded_types: set[str] = set()
    json_fields: set[str] = set()
    json_candidates = 0
    for script in scripts:
        kind = str(script.get("type", "")).split(";", 1)[0].strip().lower()
        body = script.string or script.get_text()
        payload: Any
        if kind == "application/ld+json":
            json_ld += 1
            try:
                payload = json.loads(body)
            except (ValueError, RecursionError):
                invalid_json += 1
                continue
        elif kind == "application/json":
            embedded += 1
            try:
                payload = json.loads(body)
            except (ValueError, RecursionError):
                invalid_json += 1
                continue
        else:
            match = JSON_ASSIGNMENT.search(body)
            if not match:
                continue
            try:
                payload, _ = json.JSONDecoder().raw_decode(body[match.end():])
            except (ValueError, RecursionError):
                continue
            embedded += 1
        types, fields, count = _inspect_json(payload)
        (json_ld_types if kind == "application/ld+json" else embedded_types).update(types)
        json_fields.update(fields)
        json_candidates += count

    for script in soup(["script", "style", "noscript", "template"]):
        script.decompose()
    visible_text = soup.get_text(" ", strip=True)
    restriction_markers = [
        label for label, pattern in (
            ("captcha", r"\bcaptcha\b"),
            ("acesso negado", r"\b(?:access denied|acesso negado)\b"),
            ("login", r"\b(?:sign in|log in|faça login)\b"),
        )
        if re.search(pattern, visible_text, re.I)
    ]
    tags = soup.find_all(True)
    data_names: Counter[str] = Counter()
    candidates: list[tuple[Tag, set[str]]] = []
    selectors: dict[str, Counter[str]] = {name: Counter() for name in SIGNALS}
    signals_by_tag: dict[int, set[str]] = {}
    for tag in tags:
        attribute_words = " ".join(
            f"{key} {' '.join(value) if isinstance(value, list) else value}"
            for key, value in tag.attrs.items()
            if key in {"class", "itemprop", "role"} or key.startswith("data-")
        )
        direct_text = " ".join(str(item) for item in tag.find_all(string=True, recursive=False))
        local_signals = _signals(attribute_words + " " + direct_text)
        if tag.name in {"h1", "h2", "h3", "h4"} and direct_text.strip():
            local_signals.add("titulo")
        if tag.name == "a" and tag.has_attr("href"):
            local_signals.add("url")
        signals_by_tag[id(tag)] = local_signals
        for field in local_signals:
            selectors[field][_selector(tag)] += 1
        for key in tag.attrs:
            if key.startswith("data-") and SAFE_NAME.fullmatch(key) and not SENSITIVE_NAME.search(key) and _signals(key):
                data_names[key] += 1

    for tag in tags:
        if tag.name not in {"article", "li", "section", "div"}:
            continue
        evidence = set(signals_by_tag[id(tag)])
        for descendant in tag.find_all(True):
            evidence.update(signals_by_tag[id(descendant)])
        if len(evidence) >= 3 and evidence & {"titulo", "url"} and evidence & {"preco", "area", "quartos", "banheiros", "vagas"}:
            candidates.append((tag, evidence))

    candidate_ids = {id(tag) for tag, _ in candidates}
    smallest = [
        (tag, fields) for tag, fields in candidates
        if not any(id(child) in candidate_ids for child in tag.find_all(True))
    ]
    return {
        "elementos_candidatos": len(smallest),
        "exemplos_candidatos": [
            {"seletor": _selector(tag), "sinais": sorted(fields)}
            for tag, fields in smallest[:MAX_EXAMPLES]
        ],
        "seletores_por_sinal": {
            field: [selector for selector, _ in counts.most_common(MAX_EXAMPLES)]
            for field, counts in selectors.items() if counts
        },
        "atributos_data_relevantes": dict(data_names.most_common(MAX_EXAMPLES)),
        "json_ld_scripts": json_ld,
        "json_embutido_blocos": embedded,
        "json_invalido": invalid_json,
        "tipos_json_ld": sorted(json_ld_types)[:20],
        "tipos_json_embutido": sorted(embedded_types)[:20],
        "sinais_json": sorted(json_fields),
        "objetos_json_candidatos": json_candidates,
        "scripts_total": len(scripts),
        "possiveis_restricoes": restriction_markers,
        "interpretacao": (
            "Há sinais de imóveis no HTML ou JSON; confirme manualmente os candidatos."
            if smallest or json_candidates else
            "Sem candidatos reconhecidos; a página pode depender de JavaScript ou usar outra estrutura. Não é conclusivo."
        ),
    }


def _safe_collection_error(exc: CollectionError) -> str:
    message = str(exc)
    status = re.search(r"\bHTTP (\d{3})\b", message)
    if status:
        resource = "robots.txt" if "robots.txt" in message else "página"
        return f"Acesso ao recurso {resource} recusado ou indisponível: HTTP {status.group(1)}."
    for marker, description in (
        ("robots.txt", "Acesso não permitido ou regras do robots.txt incompatíveis."),
        ("Timeout", "Tempo limite de requisição excedido."),
        ("Erro de conexão", "Erro de conexão."),
        ("URL", "URL configurada inválida."),
        ("conteúdo HTML vazio", "Resposta HTML vazia."),
        ("Resposta não é HTML", "A resposta não é HTML."),
    ):
        if marker in message:
            return description
    return "Falha na coleta HTTP; consulte a configuração e as condições de acesso."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnosticar a estrutura de uma página HTML.")
    parser.add_argument("--html-file", type=Path, help="Ler HTML local em UTF-8, sem acessar a rede.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        if args.html_file:
            html = args.html_file.read_text(encoding="utf-8")
        else:
            settings = Settings.from_env()
            client = HttpClient(settings)
            http_logger = logging.getLogger("src.collectors.http_client")
            previous_disabled = http_logger.disabled
            http_logger.disabled = True
            try:
                html = client.fetch_page()
            finally:
                http_logger.disabled = previous_disabled
                client.close()
        report = diagnose_html(html)
    except CollectionError as exc:
        logger.error("Diagnóstico não realizado: %s", _safe_collection_error(exc))
        return 1
    except (OSError, UnicodeError):
        logger.error("Diagnóstico local não realizado: erro de leitura.")
        return 1
    except ValueError as exc:
        if args.html_file:
            logger.error("Diagnóstico local não realizado: HTML vazio.")
        elif "URL da fonte precisa ser configurada" in str(exc):
            logger.error("Diagnóstico não realizado: configure SOURCE_URL.")
        elif "USER_AGENT" in str(exc):
            logger.error("Diagnóstico não realizado: configure USER_AGENT.")
        else:
            logger.error("Diagnóstico não realizado: configuração inválida ou HTML vazio.")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
