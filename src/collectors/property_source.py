from typing import Protocol

from src.models.property import Property


class PropertySource(Protocol):
    def collect(self) -> list[Property]:
        """Obtém e interpreta os imóveis de uma fonte."""
        ...
