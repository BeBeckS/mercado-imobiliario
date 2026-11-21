from pathlib import Path

from src.collectors.property_parser import parse_properties
from src.models.property import Property


class LocalPropertySource:
    """Fonte para testes e validação offline com HTML sintético."""

    def __init__(self, path: Path, base_url: str | None = None) -> None:
        self.path = path
        self.base_url = base_url

    def collect(self) -> list[Property]:
        html = self.path.read_text(encoding="utf-8")
        return parse_properties(html, base_url=self.base_url, source="Fixture local")
