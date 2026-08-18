"""Dataset layout, atomic writes, resume checks, and metadata assembly."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SCHEMA_VERSION


CSV_COLUMNS = [
    "record_id",
    "image",
    "description",
    "description_status",
    "description_source",
    "description_source_url",
    "description_language",
    "title",
    "object_type",
    "category",
    "classification",
    "creator_count",
    "creator_name",
    "creator_role",
    "creator_attribution",
    "creator_suffix",
    "creator_sort_name",
    "creator_biography",
    "creator_nationality",
    "creator_birth_year",
    "creator_death_year",
    "creator_gender",
    "creator_ulan_url",
    "creator_wikidata_url",
    "creation_date",
    "creation_start_year",
    "creation_end_year",
    "material",
    "dimensions",
    "culture",
    "period",
    "dynasty",
    "reign",
    "portfolio",
    "country",
    "region",
    "subregion",
    "locale",
    "city",
    "state",
    "county",
    "geography_type",
    "locus",
    "excavation",
    "river",
    "brand",
    "model",
    "catalog_number",
    "accession_year",
    "department",
    "repository",
    "gallery_number",
    "tags",
    "tag_count",
    "tag_aat_urls",
    "tag_wikidata_urls",
    "object_wikidata_url",
    "link_resource",
    "is_highlight",
    "is_timeline_work",
    "target_person",
    "target_architecture",
    "target_painting",
    "wikidata_entity_id",
    "wikidata_label",
    "source_key",
    "source_name",
    "source_object_id",
    "source_api_url",
    "source_page_url",
    "source_retrieved_at",
    "source_raw_path",
    "image_role",
    "image_status",
    "image_source_url",
    "image_sha256",
    "image_bytes",
    "image_content_type",
    "primary_image_small_url",
    "additional_image_count",
    "additional_image_urls",
    "rights_public_domain",
    "rights_text",
    "rights_credit_line",
    "annotation_status",
    "annotation_method",
    "annotation_reviewed",
    "annotation_note",
    "constituent_count",
    "constituent_names",
    "constituent_roles",
    "constituent_genders",
    "constituent_ulan_urls",
    "constituent_wikidata_urls",
    "metadata_date",
    "crawler_version",
    "schema_version",
]


def _nested(value: dict[str, Any], *keys: str) -> Any:
    """Return a nested dictionary value, or None when any level is absent."""
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _csv_value(value: Any) -> str | int | float:
    """Convert scalar canonical values to stable CSV cell values."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        raise ValueError("nested JSON values must be flattened before CSV export")
    return value


def _record_or_source_metadata(record: dict[str, Any], key: str) -> Any:
    """Read a promoted canonical field, falling back to legacy source_metadata."""
    value = record.get(key)
    return value if value is not None else _nested(record, "source_metadata", key)


def _first_creator(record: dict[str, Any], key: str) -> Any:
    """Return a convenience scalar from the first creator while retaining creators JSON."""
    creators = record.get("creators")
    if not isinstance(creators, list) or not creators or not isinstance(creators[0], dict):
        return None
    return creators[0].get(key)


def _list_count(value: Any) -> int:
    """Return the number of entries in a list, treating missing values as zero."""
    return len(value) if isinstance(value, list) else 0


def _joined_values(value: Any) -> str:
    """Join scalar list values for a CSV cell without embedding JSON."""
    if not isinstance(value, list):
        return ""
    return " | ".join(str(item) for item in value if item is not None and item != "")


def _joined_dict_values(value: Any, key: str) -> str:
    """Join one scalar field from each dictionary in a list, preserving list order."""
    if not isinstance(value, list):
        return ""
    return " | ".join(
        str(item[key])
        for item in value
        if isinstance(item, dict) and item.get(key) is not None and item.get(key) != ""
    )


def record_to_csv_row(record: dict[str, Any]) -> dict[str, str | int | float]:
    """Flatten one canonical record into the documented training-friendly CSV schema."""
    values: dict[str, Any] = {
        "record_id": record.get("record_id"),
        "image": _nested(record, "image", "local_path"),
        "description": record.get("description"),
        "description_status": record.get("description_status"),
        "description_source": record.get("description_source"),
        "description_source_url": record.get("description_source_url"),
        "description_language": record.get("description_language"),
        "title": record.get("title"),
        "object_type": record.get("object_type"),
        "category": record.get("category"),
        "classification": record.get("classification") or record.get("category"),
        "creator_count": _list_count(record.get("creators")),
        "creator_name": _first_creator(record, "name"),
        "creator_role": _first_creator(record, "role"),
        "creator_attribution": _first_creator(record, "attribution"),
        "creator_suffix": _first_creator(record, "suffix"),
        "creator_sort_name": _first_creator(record, "sort_name"),
        "creator_biography": _first_creator(record, "biography"),
        "creator_nationality": _first_creator(record, "nationality"),
        "creator_birth_year": _first_creator(record, "birth_year"),
        "creator_death_year": _first_creator(record, "death_year"),
        "creator_gender": _first_creator(record, "gender"),
        "creator_ulan_url": _first_creator(record, "ulan_url"),
        "creator_wikidata_url": _first_creator(record, "wikidata_url"),
        "creation_date": _nested(record, "creation_date", "display"),
        "creation_start_year": _nested(record, "creation_date", "start_year"),
        "creation_end_year": _nested(record, "creation_date", "end_year"),
        "material": record.get("material"),
        "dimensions": record.get("dimensions"),
        "culture": record.get("culture"),
        "period": record.get("period"),
        "dynasty": _record_or_source_metadata(record, "dynasty"),
        "reign": _record_or_source_metadata(record, "reign"),
        "portfolio": _record_or_source_metadata(record, "portfolio"),
        "country": record.get("country"),
        "region": _record_or_source_metadata(record, "region"),
        "subregion": _record_or_source_metadata(record, "subregion"),
        "locale": _record_or_source_metadata(record, "locale"),
        "city": _record_or_source_metadata(record, "city"),
        "state": _record_or_source_metadata(record, "state"),
        "county": _record_or_source_metadata(record, "county"),
        "geography_type": _record_or_source_metadata(record, "geography_type"),
        "locus": _record_or_source_metadata(record, "locus"),
        "excavation": _record_or_source_metadata(record, "excavation"),
        "river": _record_or_source_metadata(record, "river"),
        "brand": record.get("brand"),
        "model": record.get("model"),
        "catalog_number": record.get("catalog_number"),
        "accession_year": _record_or_source_metadata(record, "accession_year"),
        "department": record.get("department"),
        "repository": _record_or_source_metadata(record, "repository"),
        "gallery_number": _record_or_source_metadata(record, "gallery_number"),
        "tags": _joined_values(record.get("tags")),
        "tag_count": _list_count(record.get("tags")),
        "tag_aat_urls": _joined_dict_values(
            _nested(record, "source_metadata", "tag_details"), "AAT_URL"
        ),
        "tag_wikidata_urls": _joined_dict_values(
            _nested(record, "source_metadata", "tag_details"), "Wikidata_URL"
        ),
        "object_wikidata_url": _record_or_source_metadata(record, "object_wikidata_url"),
        "link_resource": _record_or_source_metadata(record, "link_resource"),
        "is_highlight": _record_or_source_metadata(record, "is_highlight"),
        "is_timeline_work": _record_or_source_metadata(record, "is_timeline_work"),
        "target_person": record.get("target_person"),
        "target_architecture": record.get("target_architecture"),
        "target_painting": record.get("target_painting"),
        "wikidata_entity_id": _nested(record, "source_metadata", "wikidata_entity_id"),
        "wikidata_label": _nested(record, "source_metadata", "wikidata_label"),
        "source_key": _nested(record, "source", "key"),
        "source_name": _nested(record, "source", "name"),
        "source_object_id": _nested(record, "source", "object_id"),
        "source_api_url": _nested(record, "source", "api_url"),
        "source_page_url": _nested(record, "source", "page_url"),
        "source_retrieved_at": _nested(record, "source", "retrieved_at"),
        "source_raw_path": _nested(record, "source", "raw_path"),
        "image_role": _nested(record, "image", "role"),
        "image_status": _nested(record, "image", "status"),
        "image_source_url": _nested(record, "image", "source_url"),
        "image_sha256": _nested(record, "image", "sha256"),
        "image_bytes": _nested(record, "image", "bytes"),
        "image_content_type": _nested(record, "image", "content_type"),
        "primary_image_small_url": _nested(record, "source_metadata", "primary_image_small_url"),
        "additional_image_count": _list_count(
            _nested(record, "source_metadata", "additional_image_urls")
        ),
        "additional_image_urls": _joined_values(
            _nested(record, "source_metadata", "additional_image_urls")
        ),
        "rights_public_domain": _nested(record, "rights", "public_domain"),
        "rights_text": _nested(record, "rights", "rights_text"),
        "rights_credit_line": _nested(record, "rights", "credit_line"),
        "annotation_status": _nested(record, "annotation", "status"),
        "annotation_method": _nested(record, "annotation", "method"),
        "annotation_reviewed": _nested(record, "annotation", "reviewed"),
        "annotation_note": _nested(record, "annotation", "note"),
        "constituent_count": _list_count(_nested(record, "source_metadata", "constituents")),
        "constituent_names": _joined_dict_values(
            _nested(record, "source_metadata", "constituents"), "name"
        ),
        "constituent_roles": _joined_dict_values(
            _nested(record, "source_metadata", "constituents"), "role"
        ),
        "constituent_genders": _joined_dict_values(
            _nested(record, "source_metadata", "constituents"), "gender"
        ),
        "constituent_ulan_urls": _joined_dict_values(
            _nested(record, "source_metadata", "constituents"), "constituentULAN_URL"
        ),
        "constituent_wikidata_urls": _joined_dict_values(
            _nested(record, "source_metadata", "constituents"), "constituentWikidata_URL"
        ),
        "metadata_date": _nested(record, "source_metadata", "metadata_date"),
        "crawler_version": record.get("crawler_version"),
        "schema_version": record.get("schema_version"),
    }
    return {column: _csv_value(values[column]) for column in CSV_COLUMNS}


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to a temporary file and atomically replace the target path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".part")
    try:
        temporary_path.write_bytes(data)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def image_extension(content_type: str | None, url: str) -> str:
    """Infer a file extension from Content-Type or the URL path, defaulting to .img."""
    normalized = (content_type or "").lower().split(";", 1)[0].strip()
    by_type = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/tiff": ".tif",
        "image/gif": ".gif",
    }
    if normalized in by_type:
        return by_type[normalized]
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else ".tif" if suffix == ".tiff" else suffix
    return ".img"


class DatasetStorage:
    def __init__(self, output_dir: Path) -> None:
        """Initialize paths for images, raw payloads, records, and manifest files."""
        self.output_dir = output_dir
        self.images_dir = output_dir / "images"
        self.raw_dir = output_dir / "raw"
        self.enrichment_dir = output_dir / "enrichment"
        self.records_dir = output_dir / "records"
        self.discovery_dir = output_dir / "discovery"
        self.state_dir = output_dir / "state"
        self.metadata_path = output_dir / "metadata.jsonl"
        self.metadata_csv_path = output_dir / "metadata.csv"
        self.manifest_path = output_dir / "crawl_manifest.jsonl"

    def prepare(self, source_key: str) -> None:
        """Create per-source subdirectories under images, raw, and records."""
        (self.images_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.raw_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.enrichment_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.records_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.discovery_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.state_dir / source_key).mkdir(parents=True, exist_ok=True)

    def raw_path(self, source_key: str, file_stem: str) -> Path:
        """Return the path for a source object's raw JSON payload."""
        return self.raw_dir / source_key / f"{file_stem}.json"

    def record_path(self, source_key: str, file_stem: str) -> Path:
        """Return the path for a canonical record JSON file."""
        return self.records_dir / source_key / f"{file_stem}.json"

    def enrichment_path(self, source_key: str, file_stem: str) -> Path:
        """Return the path for a source object's cached enrichment response."""
        return self.enrichment_dir / source_key / f"{file_stem}-description.json"

    def image_path(self, source_key: str, file_stem: str, extension: str) -> Path:
        """Return the path for a downloaded image file."""
        return self.images_dir / source_key / f"{file_stem}{extension}"

    def relative_path(self, path: Path) -> str:
        """Return a POSIX path relative to the dataset output directory."""
        return path.relative_to(self.output_dir).as_posix()

    def write_json(self, path: Path, value: Any) -> None:
        """Atomically write a pretty-printed JSON value to disk."""
        body = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        atomic_write_bytes(path, body.encode("utf-8"))

    def read_json(self, path: Path) -> dict[str, Any]:
        """Read and parse a JSON object from disk."""
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object in {path}")
        return value

    def append_manifest(self, value: dict[str, Any]) -> None:
        """Append one JSON line to the crawl manifest log."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def write_discovery(self, source_key: str, value: dict[str, Any]) -> Path:
        """Persist one reproducible discovery snapshot, including the complete ID list."""
        timestamp = datetime.now(timezone.utc)
        file_timestamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
        method = str(value.get("method") or "unknown")
        safe_method = "".join(character for character in method if character.isalnum() or character in "-_")
        path = self.discovery_dir / source_key / f"{file_timestamp}-{safe_method}.json"
        snapshot = {
            "schema_version": "1.0",
            "source": source_key,
            "discovered_at": timestamp.isoformat().replace("+00:00", "Z"),
            **value,
        }
        self.write_json(path, snapshot)
        return path

    def raw_file_timestamp(self, path: Path) -> str:
        """Return the raw file's last-modified time as an ISO 8601 UTC string."""
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return timestamp.isoformat().replace("+00:00", "Z")

    def record_is_complete(
        self,
        record_path: Path,
        skip_images: bool,
        checked_after: str | None = None,
        require_description_enrichment: bool = False,
    ) -> bool:
        """Return whether an existing record satisfies resume/skip criteria."""
        if not record_path.exists():
            return False
        try:
            record = self.read_json(record_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False

        if record.get("schema_version") != SCHEMA_VERSION:
            return False
        if require_description_enrichment and record.get("description_status") == "not_requested":
            return False

        image = record.get("image")
        if not isinstance(image, dict):
            return False
        if checked_after is not None:
            source = record.get("source")
            retrieved_at = source.get("retrieved_at") if isinstance(source, dict) else None
            if not isinstance(retrieved_at, str):
                return False
            try:
                retrieved_time = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
                required_time = datetime.fromisoformat(checked_after.replace("Z", "+00:00"))
            except ValueError:
                return False
            if retrieved_time < required_time:
                return False
        if skip_images:
            return True
        status = image.get("status")
        if status in {"no_image", "not_public_domain", "not_downloadable"}:
            return True
        local_path = image.get("local_path")
        if status == "downloaded" and isinstance(local_path, str):
            return (self.output_dir / local_path).is_file()
        return False

    def start_or_resume_refresh(
        self,
        source_key: str,
        selector: dict[str, Any],
    ) -> tuple[Path, dict[str, Any], bool]:
        """Create or resume the unfinished refresh job for a stable discovery selector."""
        selector_body = json.dumps(selector, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        selector_key = hashlib.sha256(selector_body.encode("utf-8")).hexdigest()[:16]
        path = self.state_dir / source_key / f"refresh-{selector_key}.json"
        if path.exists():
            state = self.read_json(path)
            if state.get("status") == "in_progress" and state.get("selector") == selector:
                return path, state, True

        now = datetime.now(timezone.utc)
        started_at = now.isoformat().replace("+00:00", "Z")
        state = {
            "schema_version": "1.0",
            "job_id": f"{now.strftime('%Y%m%dT%H%M%S.%fZ')}-{selector_key}",
            "source": source_key,
            "selector": selector,
            "started_at": started_at,
            "status": "in_progress",
            "last_run_at": None,
            "last_summary": None,
        }
        self.write_json(path, state)
        return path, state, False

    def update_refresh_job(
        self,
        path: Path,
        *,
        completed: bool,
        summary: dict[str, Any],
    ) -> None:
        """Record refresh progress; a completed selector starts a new job on its next run."""
        state = self.read_json(path)
        state["status"] = "completed" if completed else "in_progress"
        state["last_run_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state["last_summary"] = summary
        self.write_json(path, state)

    def rebuild_metadata(self) -> int:
        """Rebuild metadata.jsonl and metadata.csv, returning the record count."""
        records: list[dict[str, Any]] = []
        source_directories = (
            [path for path in self.records_dir.iterdir() if path.is_dir()]
            if self.records_dir.exists()
            else []
        )
        for source_directory in source_directories:
            for path in source_directory.rglob("*.json"):
                try:
                    value = self.read_json(path)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    raise ValueError(f"cannot rebuild metadata: invalid record file {path}") from error
                records.append(value)
        records.sort(key=lambda record: str(record.get("record_id", "")))
        body = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        )
        atomic_write_bytes(self.metadata_path, body.encode("utf-8"))

        csv_body = io.StringIO(newline="")
        writer = csv.DictWriter(csv_body, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(record_to_csv_row(record) for record in records)
        atomic_write_bytes(self.metadata_csv_path, csv_body.getvalue().encode("utf-8"))
        return len(records)
