from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models.listing import Listing
from src.utils.hashing import record_hash


def test_model_validates_nulls_and_constraints() -> None:
    values = {"fonte": "VivaReal", "data_coleta": datetime.now(timezone.utc), "hash_registro": "a" * 64}
    assert Listing(**values).preco is None
    for invalid in (
        {"preco": -1},
        {"quartos": -1},
        {"quartos": 1.5},
        {"url": "javascript:alert(1)"},
        {"hash_registro": "invalid"},
        {"data_coleta": datetime(2026, 1, 1)},
    ):
        with pytest.raises(ValidationError):
            Listing(**(values | invalid))


def test_hash_is_stable_and_changes_with_content() -> None:
    record = {"titulo": "Imóvel", "preco": "100", "data_coleta": "primeira"}
    assert record_hash(record) == record_hash({"preco": "100", "titulo": "Imóvel", "data_coleta": "segunda"})
    assert record_hash(record) != record_hash(record | {"preco": "200"})
