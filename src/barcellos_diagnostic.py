"""Diagnóstico estrutural da página de listagem, sem extrair imóveis."""

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from src.collectors.diagnostic import SAFE_NAME, SENSITIVE_NAME, _safe_collection_error, diagnose_html
from src.collectors.http_client import CollectionError, HttpClient
from src.config.settings import Settings

logger = logging.getLogger(__name__)

FIELDS = (
    "codigo", "titulo_ou_tipo", "bairro", "cidade", "uf", "area", "quartos",
    "suites", "banheiros", "vagas", "preco_venda", "condominio", "iptu",
    "url", "identificador_adicional",
)


def _site_config(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script"):
        body = script.string or script.get_text()
        match = re.search(r"\bwindow\.SAMI_SITE\s*=\s*", body)
        if not match:
            continue
        try:
            payload, _ = json.JSONDecoder().raw_decode(body[match.end():])
        except (json.JSONDecodeError, RecursionError):
            return {"estado": "invalido", "chaves": [], "por_pagina": None}
        if not isinstance(payload, dict):
            return {"estado": "invalido", "chaves": [], "por_pagina": None}
        keys = [key for key in ("apiImoveis", "apiFiltros", "perPage") if key in payload]
        per_page = payload.get("perPage")
        return {
            "estado": "encontrado",
            "chaves": keys,
            "por_pagina": per_page if isinstance(per_page, int) and not isinstance(per_page, bool) else None,
        }
    return {"estado": "ausente", "chaves": [], "por_pagina": None}


def diagnose_barcellos(html: str) -> dict[str, Any]:
    if not html.strip():
        raise ValueError("HTML vazio; não há estrutura para diagnosticar.")
    soup = BeautifulSoup(html, "html.parser")
    generic = diagnose_html(html)
    container = soup.select_one("div#lista-imoveis.grid-imoveis")
    if container is None:
        container_state = "ausente"
        confirmed_cards: int | None = None
        children: int | None = None
    elif not container.find(True) and not container.get_text(strip=True):
        container_state = "vazio"
        confirmed_cards = 0
        children = 0
    else:
        container_state = "nao_vazio"
        confirmed_cards = None
        children = len(container.find_all(True, recursive=False))

    links = {"relativos": 0, "absolutos_http": 0, "fragmentos": 0, "vazios": 0, "outros": 0}
    if container is not None:
        for link in container.select("a[href]"):
            href = str(link.get("href", "")).strip()
            if not href:
                links["vazios"] += 1
            elif href.startswith("#"):
                links["fragmentos"] += 1
            else:
                try:
                    parsed = urlsplit(href)
                    if parsed.scheme in ("http", "https") and parsed.hostname:
                        links["absolutos_http"] += 1
                    elif not parsed.scheme and not parsed.netloc:
                        links["relativos"] += 1
                    else:
                        links["outros"] += 1
                except ValueError:
                    links["outros"] += 1

    scripts = {
        urlsplit(str(script.get("src"))).path.rsplit("/", 1)[-1]
        for script in soup.find_all("script", src=True)
    }
    data_attributes = sorted({
        name
        for tag in ([container] + container.find_all(True) if container is not None else [])
        for name in tag.attrs
        if name.startswith("data-") and SAFE_NAME.fullmatch(name) and not SENSITIVE_NAME.search(name)
    })
    return {
        "container": {
            "seletor": "div#lista-imoveis.grid-imoveis",
            "estado": container_state,
            "elementos_filhos_diretos": children,
        },
        "cards": {
            "confirmados": confirmed_cards,
            "seletor_individual": None,
            "motivo": "Nenhum seletor de cartão individual foi confirmado nesta resposta.",
        },
        "campos_dos_cards": {
            "cards_avaliados": 0,
            "campos": list(FIELDS),
            "ausentes": None,
            "presentes_vazios": None,
            "nao_convertiveis": None,
            "motivo": "Sem cartões individuais identificados, não é possível classificar os campos.",
        },
        "links_no_container": links,
        "dados_estruturados": {
            "json_ld_scripts": generic["json_ld_scripts"],
            "tipos_json_ld": generic["tipos_json_ld"],
            "json_embutido_blocos": generic["json_embutido_blocos"],
            "json_invalido": generic["json_invalido"],
            "elementos_itemprop": len(soup.select("[itemprop]")),
            "atributos_data_no_container": data_attributes,
            "metadados_pagina": [
                name for name in ("description", "og:title", "og:description", "og:url")
                if soup.select_one(f'meta[name="{name}"], meta[property="{name}"]') is not None
            ],
            "configuracao_site": _site_config(soup),
        },
        "carregamento": {
            "sentinela": soup.select_one("#scroll-sentinela.scroll-sentinela") is not None,
            "indicador": soup.select_one("#scroll-loading.scroll-loading") is not None,
            "script_busca": "busca.js" in scripts,
            "script_scroll_infinito": "scroll-infinito.js" in scripts,
        },
        "possiveis_restricoes": generic["possiveis_restricoes"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnosticar a estrutura da listagem Barcellos.")
    parser.add_argument("--html-file", type=Path, help="Analisar HTML local em UTF-8, sem rede.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        if args.html_file:
            html = args.html_file.read_text(encoding="utf-8")
        else:
            client = HttpClient(Settings.from_env())
            http_logger = logging.getLogger("src.collectors.http_client")
            previous_disabled = http_logger.disabled
            http_logger.disabled = True
            try:
                html = client.fetch_page()
            finally:
                http_logger.disabled = previous_disabled
                client.close()
        report = diagnose_barcellos(html)
    except CollectionError as exc:
        logger.error("Diagnóstico não realizado: %s", _safe_collection_error(exc))
        return 1
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
