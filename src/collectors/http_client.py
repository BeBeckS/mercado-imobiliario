import json
import logging
import re
import time
from decimal import Decimal
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from src.config.settings import Settings, validate_source_url

logger = logging.getLogger(__name__)


def _invalid_json_constant(value: str) -> NoReturn:
    raise ValueError(f"Constante JSON inválida: {value}.")


def _safe_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return "<URL inválida>"
    if not hostname:
        return "<URL inválida>"
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _validate_robots(text: str) -> None:
    if re.search(r"<\s*!doctype\s+html\b|<\s*/?\s*(?:html|head|body|title|script|form|iframe)\b", text, re.IGNORECASE):
        raise CollectionError("robots.txt inválido: resposta contém HTML.")
    has_policy = False
    for line in text.lstrip("\ufeff").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, separator, value = line.partition(":")
        name, value = name.strip().lower(), value.strip()
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise CollectionError("robots.txt inválido: conteúdo incompatível com diretivas.")
        if name == "user-agent":
            if not value:
                raise CollectionError("robots.txt inválido: User-agent vazio.")
            has_policy = True
        elif name == "sitemap" and value:
            has_policy = True
    if not has_policy:
        raise CollectionError("robots.txt não pôde ser validado: nenhuma diretiva identificável.")


class CollectionError(Exception):
    """Falha de acesso ou de interpretação da fonte."""


class HttpClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        logging.getLogger("urllib3").setLevel(logging.ERROR)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self._last_request: float | None = None
        self._json_robots: dict[str, RobotFileParser] = {}

    def close(self) -> None:
        self.session.close()

    def _get(self, url: str) -> requests.Response:
        safe_url = _safe_url(url)
        if self._last_request is not None:
            wait = self.settings.interval_seconds - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()
        logger.info("GET %s", safe_url)
        try:
            response = self.session.get(
                url, timeout=self.settings.timeout_seconds, allow_redirects=False
            )
        except requests.exceptions.InvalidURL as exc:
            raise CollectionError(f"URL inválida ao acessar GET {safe_url}: {type(exc).__name__}.") from None
        except requests.Timeout as exc:
            raise CollectionError(f"Timeout ao acessar GET {safe_url}: {type(exc).__name__}.") from None
        except requests.ConnectionError as exc:
            raise CollectionError(f"Erro de conexão ao acessar GET {safe_url}: {type(exc).__name__}.") from None
        except requests.RequestException as exc:
            raise CollectionError(f"Falha ao acessar GET {safe_url}: {type(exc).__name__}.") from None
        logger.info("GET %s HTTP %d", safe_url, response.status_code)
        if response.status_code != 200:
            raise CollectionError(
                f"Acesso a GET {safe_url} recusado ou indisponível: HTTP {response.status_code}. "
                "Redirecionamentos e bloqueios não são contornados."
            )
        return response

    def _check_robots(self, url: str, *, cache: bool = False) -> None:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        robots = self._json_robots.get(robots_url) if cache else None
        if robots is None:
            logger.info("Consultando regras de acesso em %s", _safe_url(robots_url))
            robots_response = self._get(robots_url)
            _validate_robots(robots_response.text)
            robots = RobotFileParser()
            robots.parse(robots_response.text.lstrip("\ufeff").splitlines())
            if cache:
                self._json_robots[robots_url] = robots
        if not robots.can_fetch(self.settings.user_agent, url):
            raise CollectionError("Coleta proibida pelo robots.txt para o User-Agent configurado.")
        delay = robots.crawl_delay(self.settings.user_agent)
        if delay is not None and delay > self.settings.interval_seconds:
            raise CollectionError(
                f"robots.txt exige intervalo de {delay} segundos; ajuste REQUEST_INTERVAL_SECONDS."
            )
        if robots.request_rate(self.settings.user_agent) is not None:
            raise CollectionError(
                "robots.txt contém Request-rate; configure a coleta somente após avaliar essa regra."
            )

    def fetch_page(self) -> str:
        try:
            source_url = validate_source_url(self.settings.source_url)
        except ValueError as exc:
            raise CollectionError(f"URL da fonte inválida: {exc}") from exc
        self._check_robots(source_url)
        logger.info("Consultando página configurada %s", _safe_url(source_url))
        response = self._get(source_url)
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            raise CollectionError(f"Resposta não é HTML: Content-Type {content_type or 'ausente'}.")
        if not response.text.strip():
            raise CollectionError("A página da fonte retornou conteúdo HTML vazio.")
        return response.text

    def fetch_json(self, url: str) -> Any:
        try:
            url = validate_source_url(url)
            base_url = validate_source_url(self.settings.source_url)
            target, base = urlsplit(url), urlsplit(base_url)
            if (target.scheme, target.hostname, target.port) != (base.scheme, base.hostname, base.port):
                raise ValueError("A URL JSON deve pertencer à origem configurada.")
        except ValueError as exc:
            raise CollectionError(f"URL da fonte inválida: {exc}") from exc
        self._check_robots(url, cache=True)
        logger.info("Consultando dados JSON da fonte configurada")
        response = self._get(url)
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise CollectionError(f"Resposta não é JSON: Content-Type {content_type or 'ausente'}.")
        try:
            return json.loads(response.text, parse_float=Decimal, parse_constant=_invalid_json_constant)
        except (ValueError, RecursionError) as exc:
            raise CollectionError("Resposta JSON inválida.") from exc
