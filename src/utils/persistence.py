import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from src.models.listing import Listing
from src.models.property import Property
from src.utils.property_identity import merge_property


class PersistenceDataError(ValueError):
    """Arquivo de imóveis não contém dados JSON válidos para o modelo."""


_properties_adapter = TypeAdapter(list[Property])


def _save_records(records: list[dict[str, Any]], output_dir: Path, collected_at: datetime) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = collected_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = output_dir / f"anuncios_{timestamp}_{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return path


def save_listings(listings: list[Listing], output_dir: Path, collected_at: datetime) -> Path:
    return _save_records([item.model_dump(mode="json") for item in listings], output_dir, collected_at)


def load_properties(path: Path) -> list[Property]:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return []
    try:
        return _properties_adapter.validate_json(content)
    except ValidationError as exc:
        raise PersistenceDataError(f"Arquivo de imóveis inválido: {path} (JSON ou dados inválidos).") from exc


def _write_json_atomically(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".properties_", suffix=".tmp", delete=False
        ) as file:
            temporary_path = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def save_property_snapshot(
    properties: list[Property], output_dir: Path, source: str, collected_at: datetime
) -> Path:
    if collected_at.utcoffset() is None:
        raise ValueError("A data da coleta deve incluir fuso horário.")
    timestamp = collected_at.astimezone(timezone.utc)
    name = f"properties_{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex}.json"
    path = output_dir / "snapshots" / name
    content = json.dumps({
        "data_coleta": timestamp.isoformat(),
        "fonte": source,
        "quantidade": len(properties),
        "imoveis": [prop.model_dump(mode="json") for prop in properties],
    }, ensure_ascii=False, indent=2) + "\n"
    _write_json_atomically(content, path)
    return path


def save_properties(properties: list[Property], path: Path) -> Path:
    merged: list[Property] = []
    for prop in load_properties(path):
        merge_property(merged, prop)
    for prop in properties:
        merge_property(merged, prop, prefer_incoming=True)
    content = json.dumps(
        [prop.model_dump(mode="json") for prop in merged], ensure_ascii=False, indent=2
    ) + "\n"
    _write_json_atomically(content, path)
    return path
