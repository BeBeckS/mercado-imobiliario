import hashlib
import json
from typing import Any, Mapping


def record_hash(record: Mapping[str, Any]) -> str:
    stable = {key: value for key, value in record.items() if key not in {"data_coleta", "hash_registro"}}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
