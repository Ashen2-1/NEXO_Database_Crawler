"""Dataset layout, atomic writes, resume checks, and JSONL assembly."""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".part")
    try:
        temporary_path.write_bytes(data)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def image_extension(content_type: str | None, url: str) -> str:
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
        self.output_dir = output_dir
        self.images_dir = output_dir / "images"
        self.raw_dir = output_dir / "raw"
        self.records_dir = output_dir / "records"
        self.metadata_path = output_dir / "metadata.jsonl"
        self.manifest_path = output_dir / "crawl_manifest.jsonl"

    def prepare(self, source_key: str) -> None:
        (self.images_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.raw_dir / source_key).mkdir(parents=True, exist_ok=True)
        (self.records_dir / source_key).mkdir(parents=True, exist_ok=True)

    def raw_path(self, source_key: str, file_stem: str) -> Path:
        return self.raw_dir / source_key / f"{file_stem}.json"

    def record_path(self, source_key: str, file_stem: str) -> Path:
        return self.records_dir / source_key / f"{file_stem}.json"

    def image_path(self, source_key: str, file_stem: str, extension: str) -> Path:
        return self.images_dir / source_key / f"{file_stem}{extension}"

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.output_dir).as_posix()

    def write_json(self, path: Path, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        atomic_write_bytes(path, body.encode("utf-8"))

    def read_json(self, path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object in {path}")
        return value

    def append_manifest(self, value: dict[str, Any]) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")

    def raw_file_timestamp(self, path: Path) -> str:
        timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return timestamp.isoformat().replace("+00:00", "Z")

    def record_is_complete(self, record_path: Path, skip_images: bool) -> bool:
        if not record_path.exists():
            return False
        try:
            record = self.read_json(record_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False

        image = record.get("image")
        if not isinstance(image, dict):
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

    def rebuild_metadata(self) -> int:
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
        return len(records)
