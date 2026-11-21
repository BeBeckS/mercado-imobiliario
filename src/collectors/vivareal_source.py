from src.collectors.http_client import HttpClient
from src.collectors.property_parser import parse_properties
from src.config.settings import Settings
from src.models.property import Property


class VivaRealSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def collect(self) -> list[Property]:
        client = HttpClient(self.settings)
        try:
            html = client.fetch_page()
        finally:
            client.close()
        return parse_properties(html, base_url=self.settings.source_url, source="VivaReal")
