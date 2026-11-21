import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv


def validate_source_url(source_url: str) -> str:
    source_url = source_url.strip()
    if not source_url:
        raise ValueError("A URL da fonte precisa ser configurada em SOURCE_URL.")
    try:
        parsed = urlparse(source_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("SOURCE_URL deve ser uma URL HTTP(S) válida, sem credenciais.") from exc
    if (
        parsed.scheme not in ("http", "https")
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in source_url)
        or (port is not None and port == 0)
    ):
        raise ValueError("SOURCE_URL deve ser uma URL HTTP(S) válida, sem credenciais.")
    return source_url


@dataclass(frozen=True)
class Settings:
    source_url: str
    user_agent: str
    timeout_seconds: float
    interval_seconds: float
    output_dir: Path
    log_level: str
    property_source: Literal["vivareal", "local", "barcellos"] = "vivareal"
    local_html_path: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        source_name = os.getenv("PROPERTY_SOURCE", "vivareal").strip().lower()
        property_source: Literal["vivareal", "local", "barcellos"]
        if source_name == "vivareal":
            property_source = "vivareal"
        elif source_name == "local":
            property_source = "local"
        elif source_name == "barcellos":
            property_source = "barcellos"
        else:
            raise ValueError("PROPERTY_SOURCE inválida. Valores permitidos: vivareal, local, barcellos.")
        source_url = os.getenv("SOURCE_URL", "").strip()
        if property_source in ("vivareal", "barcellos"):
            source_url = validate_source_url(source_url)
        local_html_path = None
        if property_source == "local":
            local_path = os.getenv("LOCAL_HTML_PATH", "").strip()
            if not local_path:
                raise ValueError("Configure LOCAL_HTML_PATH para a fonte local.")
            local_html_path = Path(local_path)
        try:
            timeout = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15"))
            interval = float(os.getenv("REQUEST_INTERVAL_SECONDS", "2"))
        except ValueError as exc:
            raise ValueError("Timeout e intervalo devem ser números.") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("REQUEST_TIMEOUT_SECONDS deve ser positivo e finito.")
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("REQUEST_INTERVAL_SECONDS deve ser não negativo e finito.")
        user_agent = os.getenv("USER_AGENT", "").strip()
        if not user_agent or "\n" in user_agent or "\r" in user_agent:
            raise ValueError("Configure USER_AGENT com identificação e contato válidos.")
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL inválido.")
        return cls(
            source_url=source_url,
            user_agent=user_agent,
            timeout_seconds=timeout,
            interval_seconds=interval,
            output_dir=Path(os.getenv("OUTPUT_DIR", "data")),
            log_level=log_level,
            property_source=property_source,
            local_html_path=local_html_path,
        )
