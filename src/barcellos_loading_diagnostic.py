"""Inspeção offline de HTML e scripts de carregamento, sem executar JavaScript."""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from src.collectors.diagnostic import SENSITIVE_NAME
from src.config.settings import validate_source_url

logger = logging.getLogger(__name__)


def _safe_url(base_url: str, value: str) -> str:
    url = validate_source_url(urljoin(base_url, value))
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _config(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script"):
        body = script.string or script.get_text()
        match = re.search(r"\bwindow\.SAMI_SITE\s*=\s*", body)
        if match:
            try:
                config, _ = json.JSONDecoder().raw_decode(body[match.end():])
            except json.JSONDecodeError as exc:
                raise ValueError("Configuração SAMI_SITE não contém JSON válido.") from exc
            if not isinstance(config, dict) or not isinstance(config.get("apiImoveis"), str):
                raise ValueError("Configuração SAMI_SITE sem apiImoveis válida.")
            return config
    raise ValueError("Configuração SAMI_SITE não encontrada.")


def diagnose_loading(html: str, search_js: str, scroll_js: str, source_url: str) -> dict[str, Any]:
    if not all(content.strip() for content in (html, search_js, scroll_js)):
        raise ValueError("HTML ou scripts vazios.")
    source_url = validate_source_url(source_url)
    soup = BeautifulSoup(html, "html.parser")
    config = _config(soup)
    scripts: dict[str, str] = {}
    for tag in soup.find_all("script", src=True):
        src = str(tag.get("src"))
        name = urlsplit(src).path.rsplit("/", 1)[-1]
        if name in ("busca.js", "scroll-infinito.js"):
            scripts[name] = _safe_url(source_url, src)

    form = soup.select_one("form#form-busca")
    filters: set[str] = set()
    if form:
        for field in form.select("[name]"):
            name = str(field.get("name"))
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\[\])?", name) and not SENSITIVE_NAME.search(name):
                filters.add(name)
        for field in form.select("[data-dropdown-nome]"):
            name = str(field.get("data-dropdown-nome"))
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and not SENSITIVE_NAME.search(name):
                filters.add(f"{name}[]")

    fetch_json = re.search(
        r"""fetch\s*\(\s*CFG\.apiImoveis\s*\+\s*['"]\?['"]\s*\+\s*\w+\.toString\(\)\s*,\s*"""
        r"""\{\s*headers\s*:\s*\{\s*['"]Accept['"]\s*:\s*['"]application/json['"]\s*\}\s*\}\s*\)""",
        scroll_js,
    )
    initial = bool(re.search(r"""\.set\(\s*['"]pagina['"]\s*,\s*1\s*\)""", scroll_js))
    next_page = bool(re.search(r"""\.set\(\s*['"]pagina['"]\s*,\s*state\.pagina\s*\+\s*1\s*\)""", scroll_js))
    per_page = config.get("perPage")
    return {
        "scripts": scripts,
        "api": {
            "url_sem_query": _safe_url(source_url, config["apiImoveis"]),
            "query_na_configuracao": bool(urlsplit(config["apiImoveis"]).query),
            "metodo": "GET" if fetch_json else None,
            "resposta_esperada": "JSON" if fetch_json and re.search(r"\b\w+\.json\(\)", scroll_js) else None,
            "parametros_do_formulario": sorted(filters),
            "finalidade_usada_pelo_script": bool(re.search(r"""params\.set\(\s*['"]finalidade['"]""", search_js)),
            "primeira_pagina": 1 if initial else None,
            "pagina_seguinte": "pagina atual + 1" if next_page else None,
            "campos_resposta_referenciados": sorted(set(re.findall(r"\bdata\.([A-Za-z_]\w*)", scroll_js))),
            "campos_item_referenciados": sorted(set(re.findall(r"\bim\.([A-Za-z_]\w*)", search_js))),
        },
        "lote_visual_configurado": (
            per_page if isinstance(per_page, int) and not isinstance(per_page, bool) and per_page > 0 else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnosticar scripts de carregamento sem acessar a rede.")
    parser.add_argument("--html-file", type=Path, required=True)
    parser.add_argument("--busca-file", type=Path, required=True)
    parser.add_argument("--scroll-file", type=Path, required=True)
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        report = diagnose_loading(
            args.html_file.read_text(encoding="utf-8"),
            args.busca_file.read_text(encoding="utf-8"),
            args.scroll_file.read_text(encoding="utf-8"),
            args.source_url,
        )
    except (OSError, UnicodeError):
        logger.error("Diagnóstico não realizado: erro de leitura.")
        return 1
    except ValueError as exc:
        logger.error("Diagnóstico não realizado: %s", exc)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
