import logging
import sys
from datetime import datetime, timezone

from src.collectors.barcellos_source import BarcellosSource
from src.collectors.http_client import CollectionError
from src.collectors.local_source import LocalPropertySource
from src.collectors.property_parser import PropertyParseError
from src.collectors.property_source import PropertySource
from src.collectors.vivareal_source import VivaRealSource
from src.config.settings import Settings
from src.utils.persistence import save_properties, save_property_snapshot


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.ERROR)
    try:
        settings = Settings.from_env()
        logging.getLogger().setLevel(settings.log_level)
        logger.setLevel(min(logging.getLevelNamesMapping()[settings.log_level], logging.ERROR))
        source: PropertySource
        if settings.property_source == "vivareal":
            source = VivaRealSource(settings)
        elif settings.property_source == "local":
            if settings.local_html_path is None:
                raise ValueError("Configure LOCAL_HTML_PATH para a fonte local.")
            source = LocalPropertySource(settings.local_html_path)
        elif settings.property_source == "barcellos":
            source = BarcellosSource(settings)
        else:
            raise ValueError("PROPERTY_SOURCE inválida. Valores permitidos: vivareal, local, barcellos.")
        properties = source.collect()
        if not properties:
            logger.error("Nenhum imóvel reconhecido; coleta não salva.")
            return 1
        snapshot_path = save_property_snapshot(
            properties, settings.output_dir, settings.property_source, datetime.now(timezone.utc)
        )
        logger.info("Snapshot da coleta salvo em %s", snapshot_path)
        try:
            path = save_properties(properties, settings.output_dir / "properties.json")
        except (ValueError, OSError):
            logger.error("Snapshot preservado em %s; falha ao atualizar o consolidado.", snapshot_path)
            raise
    except CollectionError as exc:
        if "HTTP 403" in str(exc):
            logger.error("HTTP 403: a fonte recusou a requisição; o bloqueio não será contornado.")
        else:
            logger.error("Coleta não concluída: %s", exc)
        return 1
    except PropertyParseError as exc:
        logger.error("HTML ou dados dos imóveis inválidos: %s", exc)
        return 1
    except (ValueError, OSError) as exc:
        logger.error("Coleta não concluída: %s", exc)
        return 1
    logger.info("%d imóveis salvos em %s", len(properties), path)
    print(f"Coleta concluída: {len(properties)} imóvel(is). Arquivo: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
