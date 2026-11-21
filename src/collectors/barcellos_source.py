"""Fonte de imóveis da Barcellos através do cliente HTTP compartilhado."""

import logging
from urllib.parse import urlencode, urljoin

from src.collectors.barcellos_parser import parse_barcellos_page
from src.collectors.http_client import HttpClient
from src.config.settings import Settings, validate_source_url
from src.models.property import Property
from src.utils.property_identity import merge_property

logger = logging.getLogger(__name__)


class BarcellosSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def collect(self) -> list[Property]:
        source_url = validate_source_url(self.settings.source_url)
        endpoint = urljoin(source_url, "/api/imoveis.php")
        properties: list[Property] = []
        seen_ids: set[str] = set()
        first_total: int | None = None
        page = 1
        client = HttpClient(self.settings)
        try:
            while True:
                url = f"{endpoint}?{urlencode({'finalidade': 'vendas', 'pagina': page})}"
                logger.info("Consultando página %d da Barcellos", page)
                payload = client.fetch_json(url)
                items, has_more, total = parse_barcellos_page(payload, source_url, page)
                if first_total is None:
                    first_total = total
                elif total != first_total:
                    raise ValueError("Paginação Barcellos inconsistente: total mudou durante a coleta.")
                if not items and (has_more or page > 1):
                    raise ValueError("Página Barcellos vazia apesar de haver próxima página.")
                page_ids = {prop.id_anuncio for prop in items if prop.id_anuncio is not None}
                if page > 1 and page_ids.issubset(seen_ids):
                    raise ValueError("Página Barcellos repetida ou sem novos identificadores.")
                seen_ids.update(page_ids)
                if len(seen_ids) > first_total or (has_more and len(seen_ids) >= first_total):
                    raise ValueError("Paginação Barcellos incompatível com o total inicial.")
                for prop in items:
                    merge_property(properties, prop, prefer_incoming=True)
                if not has_more:
                    if len(seen_ids) != first_total:
                        raise ValueError(
                            f"Paginação Barcellos incompleta: total informado {first_total}, "
                            f"mas {len(seen_ids)} identificadores distintos coletados."
                        )
                    logger.info("Barcellos: %d imóveis distintos processados", len(properties))
                    return properties
                page += 1
        finally:
            client.close()
