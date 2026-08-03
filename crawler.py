#!/usr/bin/env python3
"""Download source-grounded image metadata from The Metropolitan Museum of Art.

This crawler deliberately does not generate captions or inferred annotations. Every
descriptive field in its output comes directly from the Met Collection API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"
CRAWLER_VERSION = "0.1.0"
DEFAULT_USER_AGENT = "NEXO-Database-Crawler/0.1 (Met Museum research dataset)"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".part")
    try:
        temporary_path.write_bytes(data)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, data)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


@dataclass
class HttpResponse:
    data: bytes
    content_type: str | None
    final_url: str


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float,
        retries: int,
        request_delay: float,
        user_agent: str,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.request_delay = request_delay
        self.user_agent = user_agent
        self._last_request_started: float | None = None

    def _throttle(self) -> None:
        if self._last_request_started is not None:
            elapsed = time.monotonic() - self._last_request_started
            if elapsed < self.request_delay:
                time.sleep(self.request_delay - elapsed)
        self._last_request_started = time.monotonic()

    def get(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json,image/*;q=0.9,*/*;q=0.8",
                    "User-Agent": self.user_agent,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    content_type = response.headers.get_content_type()
                    return HttpResponse(
                        data=response.read(),
                        content_type=content_type,
                        final_url=response.geturl(),
                    )
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code not in RETRYABLE_STATUS_CODES or attempt >= self.retries:
                    raise
                retry_after = error.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                time.sleep(min(delay, 30.0))
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
                if attempt >= self.retries:
                    raise
                time.sleep(min(2**attempt, 30.0))
        raise RuntimeError(f"request failed: {last_error}")

    def get_json(self, url: str) -> dict[str, Any]:
        response = self.get(url)
        try:
            value = json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid JSON response from {url}") from error
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object from {url}")
        return value


def ordered_unique(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def read_object_ids(path: Path) -> list[int]:
    object_ids: list[int] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            object_id = int(line)
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: expected one numeric object ID per line") from error
        if object_id <= 0:
            raise ValueError(f"{path}:{line_number}: object ID must be positive")
        object_ids.append(object_id)
    return object_ids


def search_object_ids(
    client: HttpClient,
    query: str,
    department_id: int | None,
    has_images: bool,
) -> list[int]:
    parameters: dict[str, str | int] = {"q": query}
    if has_images:
        parameters["hasImages"] = "true"
    if department_id is not None:
        parameters["departmentId"] = department_id
    url = f"{API_BASE}/search?{urllib.parse.urlencode(parameters)}"
    response = client.get_json(url)
    values = response.get("objectIDs") or []
    if not isinstance(values, list):
        raise ValueError("Met search response contained an invalid objectIDs field")
    return [value for value in values if isinstance(value, int) and value > 0]


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


def normalize_record(
    payload: dict[str, Any],
    *,
    fetched_at: str,
    raw_source_path: str,
    image_info: dict[str, Any],
) -> dict[str, Any]:
    """Map the Met payload without inventing or interpreting any fields."""
    object_id = payload.get("objectID")
    if not isinstance(object_id, int):
        raise ValueError("Met object response has no numeric objectID")

    return {
        "schema_version": "1.0",
        "record_id": f"MET-{object_id}",
        "source_site": "The Metropolitan Museum of Art",
        "source_object_id": object_id,
        "source_api_url": f"{API_BASE}/objects/{object_id}",
        "source_page_url": payload.get("objectURL"),
        "retrieved_at": fetched_at,
        "raw_source_path": raw_source_path,
        "image": image_info.get("local_path"),
        "image_status": image_info.get("status"),
        "image_sha256": image_info.get("sha256"),
        "image_bytes": image_info.get("bytes"),
        "image_content_type": image_info.get("content_type"),
        "primary_image_url": payload.get("primaryImage"),
        "primary_image_small_url": payload.get("primaryImageSmall"),
        "additional_image_urls": payload.get("additionalImages") or [],
        "is_public_domain": payload.get("isPublicDomain"),
        "title": payload.get("title"),
        "object_name": payload.get("objectName"),
        "object_date_raw": payload.get("objectDate"),
        "object_begin_date": payload.get("objectBeginDate"),
        "object_end_date": payload.get("objectEndDate"),
        "medium_raw": payload.get("medium"),
        "dimensions_raw": payload.get("dimensions"),
        "department": payload.get("department"),
        "classification": payload.get("classification"),
        "culture": payload.get("culture"),
        "period": payload.get("period"),
        "dynasty": payload.get("dynasty"),
        "reign": payload.get("reign"),
        "portfolio": payload.get("portfolio"),
        "credit_line": payload.get("creditLine"),
        "repository": payload.get("repository"),
        "rights_and_reproduction": payload.get("rightsAndReproduction"),
        "accession_number": payload.get("accessionNumber"),
        "accession_year": payload.get("accessionYear"),
        "creator": {
            "role": payload.get("artistRole"),
            "prefix": payload.get("artistPrefix"),
            "display_name": payload.get("artistDisplayName"),
            "display_bio": payload.get("artistDisplayBio"),
            "suffix": payload.get("artistSuffix"),
            "alpha_sort": payload.get("artistAlphaSort"),
            "nationality": payload.get("artistNationality"),
            "begin_date": payload.get("artistBeginDate"),
            "end_date": payload.get("artistEndDate"),
            "gender": payload.get("artistGender"),
            "ulan_url": payload.get("artistULAN_URL"),
            "wikidata_url": payload.get("artistWikidata_URL"),
        },
        "geography": {
            "region": payload.get("region"),
            "subregion": payload.get("subregion"),
            "locale": payload.get("locale"),
            "locus": payload.get("locus"),
            "excavation": payload.get("excavation"),
            "river": payload.get("river"),
            "county": payload.get("county"),
            "state": payload.get("state"),
            "country": payload.get("country"),
            "city": payload.get("city"),
        },
        "measurements": payload.get("measurements") or [],
        "constituents": payload.get("constituents") or [],
        "tags": payload.get("tags") or [],
        "metadata_date": payload.get("metadataDate"),
        "annotation": {
            "status": "source_only",
            "method": "none",
            "reviewed": False,
            "note": "No AI-generated or human-inferred annotations were added.",
        },
        "crawler_version": CRAWLER_VERSION,
    }


def record_is_complete(record_path: Path, output_dir: Path, skip_images: bool) -> bool:
    if not record_path.exists():
        return False
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if skip_images:
        return True
    status = record.get("image_status")
    if status in {"no_image", "not_public_domain"}:
        return True
    if status == "downloaded" and isinstance(record.get("image"), str):
        return (output_dir / record["image"]).is_file()
    return False


def rebuild_metadata(records_dir: Path, metadata_path: Path) -> int:
    records: list[dict[str, Any]] = []
    for path in records_dir.glob("MET-*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot rebuild metadata: invalid record file {path}") from error
        if isinstance(value, dict):
            records.append(value)
    records.sort(key=lambda record: int(record.get("source_object_id", 0)))
    body = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    atomic_write_bytes(metadata_path, body.encode("utf-8"))
    return len(records)


class MetCrawler:
    def __init__(self, output_dir: Path, client: HttpClient, skip_images: bool, force: bool) -> None:
        self.output_dir = output_dir
        self.client = client
        self.skip_images = skip_images
        self.force = force
        self.images_dir = output_dir / "images"
        self.raw_dir = output_dir / "raw"
        self.records_dir = output_dir / "records"
        self.manifest_path = output_dir / "crawl_manifest.jsonl"

    def prepare(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def _download_image(self, payload: dict[str, Any], record_id: str) -> dict[str, Any]:
        image_url = payload.get("primaryImage")
        if not image_url:
            return {"status": "no_image"}
        if payload.get("isPublicDomain") is not True:
            return {"status": "not_public_domain"}
        if self.skip_images:
            return {"status": "skipped"}

        response = self.client.get(str(image_url))
        extension = image_extension(response.content_type, response.final_url)
        image_path = self.images_dir / f"{record_id}{extension}"
        atomic_write_bytes(image_path, response.data)
        return {
            "status": "downloaded",
            "local_path": image_path.relative_to(self.output_dir).as_posix(),
            "sha256": hashlib.sha256(response.data).hexdigest(),
            "bytes": len(response.data),
            "content_type": response.content_type,
        }

    def crawl_one(self, object_id: int) -> str:
        record_id = f"MET-{object_id}"
        raw_path = self.raw_dir / f"{record_id}.json"
        record_path = self.records_dir / f"{record_id}.json"

        if not self.force and raw_path.exists() and record_is_complete(
            record_path, self.output_dir, self.skip_images
        ):
            append_jsonl(
                self.manifest_path,
                {
                    "timestamp": utc_now(),
                    "record_id": record_id,
                    "source_object_id": object_id,
                    "status": "skipped_existing",
                },
            )
            return "skipped"

        started_at = utc_now()
        api_url = f"{API_BASE}/objects/{object_id}"
        try:
            payload = self.client.get_json(api_url)
            if payload.get("objectID") != object_id:
                raise ValueError(f"Met returned objectID {payload.get('objectID')} for requested ID {object_id}")
            atomic_write_json(raw_path, payload)
            image_info = self._download_image(payload, record_id)
            record = normalize_record(
                payload,
                fetched_at=started_at,
                raw_source_path=raw_path.relative_to(self.output_dir).as_posix(),
                image_info=image_info,
            )
            atomic_write_json(record_path, record)
            append_jsonl(
                self.manifest_path,
                {
                    "timestamp": utc_now(),
                    "record_id": record_id,
                    "source_object_id": object_id,
                    "source_api_url": api_url,
                    "status": "completed",
                    "image_status": image_info["status"],
                },
            )
            return "completed"
        except Exception as error:
            append_jsonl(
                self.manifest_path,
                {
                    "timestamp": utc_now(),
                    "record_id": record_id,
                    "source_object_id": object_id,
                    "source_api_url": api_url,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
            raise


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download source metadata and public-domain images from the Met Collection API. "
            "No AI annotation is performed."
        )
    )
    parser.add_argument(
        "--object-id",
        action="append",
        type=positive_int,
        default=[],
        help="Met object ID; repeat this option for multiple objects",
    )
    parser.add_argument("--ids-file", type=Path, help="UTF-8 text file with one Met object ID per line")
    parser.add_argument("--query", help="Met Collection API search query")
    parser.add_argument("--department-id", type=positive_int, help="optional Met department ID for --query")
    parser.add_argument(
        "--include-results-without-images",
        action="store_true",
        help="do not add hasImages=true to a search query",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=100,
        help="maximum number of unique objects per run (default: 100)",
    )
    parser.add_argument("--output", type=Path, default=Path("dataset"), help="dataset directory")
    parser.add_argument("--skip-images", action="store_true", help="save metadata but do not download images")
    parser.add_argument("--force", action="store_true", help="fetch records again even when output is complete")
    parser.add_argument("--timeout", type=nonnegative_float, default=30.0, help="request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="retry count for temporary request failures")
    parser.add_argument(
        "--request-delay",
        type=nonnegative_float,
        default=0.25,
        help="minimum seconds between requests (default: 0.25)",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent string")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.retries < 0:
        parser.error("--retries must be zero or greater")
    if args.timeout == 0:
        parser.error("--timeout must be greater than zero")
    if args.department_id is not None and not args.query:
        parser.error("--department-id requires --query")
    if not args.object_id and args.ids_file is None and not args.query:
        parser.error("provide --object-id, --ids-file, or --query; the crawler never fetches the whole site implicitly")

    client = HttpClient(
        timeout=args.timeout,
        retries=args.retries,
        request_delay=args.request_delay,
        user_agent=args.user_agent,
    )
    object_ids: list[int] = list(args.object_id)
    try:
        if args.ids_file is not None:
            object_ids.extend(read_object_ids(args.ids_file))
        if args.query:
            print(f"Searching the Met API for {args.query!r} ...")
            object_ids.extend(
                search_object_ids(
                    client,
                    args.query,
                    args.department_id,
                    not args.include_results_without_images,
                )
            )
    except Exception as error:
        print(f"Could not collect object IDs: {error}", file=sys.stderr)
        return 1

    object_ids = ordered_unique(object_ids)[: args.limit]
    if not object_ids:
        print("No matching Met object IDs were found.")
        return 0

    output_dir = args.output.resolve()
    crawler = MetCrawler(output_dir, client, args.skip_images, args.force)
    crawler.prepare()
    print(f"Processing {len(object_ids)} object(s) into {output_dir}")

    completed = 0
    skipped = 0
    failed = 0
    for index, object_id in enumerate(object_ids, 1):
        try:
            result = crawler.crawl_one(object_id)
            if result == "skipped":
                skipped += 1
                print(f"[{index}/{len(object_ids)}] MET-{object_id}: already complete")
            else:
                completed += 1
                print(f"[{index}/{len(object_ids)}] MET-{object_id}: saved")
        except Exception as error:
            failed += 1
            print(f"[{index}/{len(object_ids)}] MET-{object_id}: failed: {error}", file=sys.stderr)

    metadata_count = rebuild_metadata(crawler.records_dir, output_dir / "metadata.jsonl")
    print(
        f"Done: {completed} completed, {skipped} skipped, {failed} failed; "
        f"metadata.jsonl contains {metadata_count} record(s)."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
