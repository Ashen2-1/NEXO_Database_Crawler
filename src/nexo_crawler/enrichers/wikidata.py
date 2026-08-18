"""Wikidata entity-description enrichment using the stable EntityData interface."""

from __future__ import annotations

import re
from typing import Any

from ..http import HttpClient


QID_PATTERN = re.compile(r"\bQ\d+\b", re.IGNORECASE)
ENTITY_DATA_BASE = "https://www.wikidata.org/wiki/Special:EntityData"


def _language_value(values: Any, language: str) -> str | None:
    """Return one non-empty Wikibase language value."""
    if not isinstance(values, dict):
        return None
    entry = values.get(language)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    return value.strip() if isinstance(value, str) and value.strip() else None


def fetch_wikidata_description(
    wikidata_url: str | None,
    client: HttpClient,
    *,
    language: str = "en",
) -> dict[str, Any]:
    """Fetch a known Wikidata entity and return its sourced label and short description."""
    match = QID_PATTERN.search(wikidata_url or "")
    if match is None:
        return {"description_status": "no_source"}

    entity_id = match.group(0).upper()
    data_url = f"{ENTITY_DATA_BASE}/{entity_id}.json"
    payload = client.get_json(data_url)
    entities = payload.get("entities")
    entity = entities.get(entity_id) if isinstance(entities, dict) else None
    if not isinstance(entity, dict):
        raise ValueError(f"Wikidata response did not contain entity {entity_id}")

    description = _language_value(entity.get("descriptions"), language)
    result = {
        "description_status": "available" if description else "not_available",
        "description": description,
        "description_source": "wikidata",
        "description_source_url": data_url,
        "description_language": language if description else None,
        "wikidata_entity_id": entity_id,
        "wikidata_label": _language_value(entity.get("labels"), language),
    }
    return result
