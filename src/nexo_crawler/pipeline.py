"""Source-independent crawling orchestration."""

from __future__ import annotations

import hashlib
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .http import HttpClient
from .models import ImageInfo
from .sources.base import ImageCandidate, NormalizationContext, SourceAdapter
from .storage import DatasetStorage, atomic_write_bytes, image_extension


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string with a Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CrawlSummary:
    discovered: int
    examined: int
    attempted: int
    completed: int
    created: int
    updated: int
    unchanged: int
    skipped: int
    filtered: int
    failed: int
    limit_reached: bool


ProgressCallback = Callable[[int, int, str, str, Exception | None], None]


class CrawlPipeline:
    def __init__(
        self,
        *,
        adapter: SourceAdapter,
        client: HttpClient,
        storage: DatasetStorage,
        skip_images: bool,
        force: bool,
        enrich_descriptions: bool = False,
        public_domain_only: bool = False,
        targets: tuple[str, ...] = (),
        refresh_after: str | None = None,
    ) -> None:
        """Wire up the adapter, HTTP client, storage layer, and crawl flags."""
        self.adapter = adapter
        self.client = client
        self.storage = storage
        self.skip_images = skip_images
        self.force = force
        self.enrich_descriptions = enrich_descriptions
        self.public_domain_only = public_domain_only
        self.targets = targets
        self.refresh_after = refresh_after

    def _record_is_complete(self, source_id: str, candidate: ImageCandidate) -> bool:
        """Return whether the canonical record for this image candidate already exists."""
        identity = self.adapter.identity(source_id, candidate)
        record_path = self.storage.record_path(self.adapter.source_key, identity.file_stem)
        return self.storage.record_is_complete(
            record_path,
            self.skip_images,
            checked_after=self.refresh_after,
            require_description_enrichment=self.enrich_descriptions,
        )

    def is_complete(self, source_id: str) -> bool:
        """Return whether all image records for a cached source object are complete."""
        if self.force and self.refresh_after is None:
            return False
        raw_path = self.storage.raw_path(
            self.adapter.source_key,
            self.adapter.raw_file_stem(source_id),
        )
        if not raw_path.exists():
            return False
        try:
            raw = self.storage.read_json(raw_path)
            candidates = self.adapter.image_candidates(source_id, raw)
        except Exception:
            return False
        return bool(candidates) and all(
            self._record_is_complete(source_id, candidate) for candidate in candidates
        )

    def _reuse_existing_image(
        self,
        source_id: str,
        candidate: ImageCandidate,
    ) -> ImageInfo | None:
        """Reuse an unchanged local image while refreshing its surrounding metadata."""
        if self.force:
            return None
        identity = self.adapter.identity(source_id, candidate)
        record_path = self.storage.record_path(self.adapter.source_key, identity.file_stem)
        if not record_path.exists():
            return None
        try:
            record = self.storage.read_json(record_path)
        except (OSError, ValueError):
            return None
        image = record.get("image")
        if not isinstance(image, dict) or image.get("source_url") != candidate.source_url:
            return None

        status = image.get("status")
        local_path = image.get("local_path")
        if status == "downloaded":
            if not isinstance(local_path, str) or not (self.storage.output_dir / local_path).is_file():
                return None
        elif status == "no_image":
            if candidate.source_url is not None:
                return None
        elif status in {"not_public_domain", "not_downloadable"}:
            if candidate.download_allowed or status != candidate.blocked_status:
                return None
        elif status == "skipped":
            if not self.skip_images:
                return None
        else:
            return None

        return ImageInfo(
            role=str(image.get("role") or candidate.role),
            status=str(status),
            source_url=candidate.source_url,
            local_path=local_path if isinstance(local_path, str) else None,
            sha256=image.get("sha256") if isinstance(image.get("sha256"), str) else None,
            bytes=image.get("bytes") if isinstance(image.get("bytes"), int) else None,
            content_type=(
                image.get("content_type") if isinstance(image.get("content_type"), str) else None
            ),
        )

    def _download_image(
        self,
        source_id: str,
        candidate: ImageCandidate,
    ) -> ImageInfo:
        """Download or skip an image candidate and return its ImageInfo status."""
        if not candidate.download_allowed:
            return ImageInfo(
                role=candidate.role,
                status=candidate.blocked_status,
                source_url=candidate.source_url,
            )
        if not candidate.source_url:
            return ImageInfo(role=candidate.role, status="no_image", source_url=None)
        if self.skip_images:
            return ImageInfo(role=candidate.role, status="skipped", source_url=candidate.source_url)

        response = self.client.get(candidate.source_url)
        identity = self.adapter.identity(source_id, candidate)
        extension = image_extension(response.content_type, response.final_url)
        image_path = self.storage.image_path(
            self.adapter.source_key,
            identity.file_stem,
            extension,
        )
        atomic_write_bytes(image_path, response.data)
        return ImageInfo(
            role=candidate.role,
            status="downloaded",
            source_url=candidate.source_url,
            local_path=self.storage.relative_path(image_path),
            sha256=hashlib.sha256(response.data).hexdigest(),
            bytes=len(response.data),
            content_type=response.content_type,
        )

    def _load_enrichment(self, source_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Load cached description enrichment or fetch and cache it when requested."""
        if not self.enrich_descriptions:
            return {}
        path = self.storage.enrichment_path(
            self.adapter.source_key,
            self.adapter.raw_file_stem(source_id),
        )
        if path.exists() and not self.force and self.refresh_after is None:
            return self.storage.read_json(path)
        enrichment = {
            "schema_version": "1.0",
            "source": self.adapter.source_key,
            "source_object_id": source_id,
            "retrieved_at": utc_now(),
            **self.adapter.enrich(source_id, raw, self.client),
        }
        self.storage.write_json(path, enrichment)
        return enrichment

    def crawl_one(self, source_id: str) -> str:
        """Fetch, normalize, and persist one source object and return its change outcome."""
        raw_path = self.storage.raw_path(
            self.adapter.source_key,
            self.adapter.raw_file_stem(source_id),
        )
        source_api_url = self.adapter.api_url(source_id)
        try:
            old_raw = self.storage.read_json(raw_path) if raw_path.exists() else None
            if self.is_complete(source_id):
                self.storage.append_manifest(
                    {
                        "timestamp": utc_now(),
                        "source": self.adapter.source_key,
                        "source_object_id": source_id,
                        "status": "skipped_existing",
                    }
                )
                return "skipped"

            fetched = old_raw is None or self.force or self.refresh_after is not None
            if fetched:
                raw = self.adapter.fetch(source_id, self.client)
                retrieved_at = utc_now()
                self.storage.write_json(raw_path, raw)
            else:
                raw = old_raw
                retrieved_at = self.storage.raw_file_timestamp(raw_path)

            if not self.adapter.matches_targets(raw, self.targets):
                self.storage.append_manifest(
                    {
                        "timestamp": utc_now(),
                        "source": self.adapter.source_key,
                        "source_object_id": source_id,
                        "source_api_url": source_api_url,
                        "status": "filtered_target_mismatch",
                        "requested_targets": list(self.targets),
                    }
                )
                return "filtered"

            if self.public_domain_only and raw.get("isPublicDomain") is not True:
                self.storage.append_manifest(
                    {
                        "timestamp": utc_now(),
                        "source": self.adapter.source_key,
                        "source_object_id": source_id,
                        "source_api_url": source_api_url,
                        "status": "filtered_not_public_domain",
                    }
                )
                return "filtered"

            enrichment = self._load_enrichment(source_id, raw)
            candidates = self.adapter.image_candidates(source_id, raw)

            if old_raw is None:
                change_status = "created"
            elif fetched and old_raw == raw:
                change_status = "unchanged"
            elif fetched:
                change_status = "updated"
            else:
                change_status = "completed"

            if not candidates:
                raise ValueError(f"{self.adapter.display_reference(source_id)} produced no image samples")

            image_statuses: list[str] = []
            for candidate in candidates:
                identity = self.adapter.identity(source_id, candidate)
                record_path = self.storage.record_path(self.adapter.source_key, identity.file_stem)
                if (
                    not self.force
                    and self.refresh_after is None
                    and self.storage.record_is_complete(
                        record_path,
                        self.skip_images,
                        require_description_enrichment=self.enrich_descriptions,
                    )
                ):
                    existing = self.storage.read_json(record_path)
                    existing_image = existing.get("image")
                    if isinstance(existing_image, dict) and isinstance(existing_image.get("status"), str):
                        image_statuses.append(existing_image["status"])
                    continue

                image = self._reuse_existing_image(source_id, candidate)
                if image is None:
                    image = self._download_image(source_id, candidate)
                context = NormalizationContext(
                    identity=identity,
                    retrieved_at=retrieved_at,
                    raw_path=self.storage.relative_path(raw_path),
                    image=image,
                    enrichment_requested=self.enrich_descriptions,
                    enrichment=enrichment,
                )
                record = self.adapter.normalize(source_id, raw, context)
                self.storage.write_json(record_path, record.to_dict())
                image_statuses.append(image.status)

            self.storage.append_manifest(
                {
                    "timestamp": utc_now(),
                    "source": self.adapter.source_key,
                    "source_object_id": source_id,
                    "source_api_url": source_api_url,
                    "status": "completed",
                    "change_status": change_status,
                    "image_statuses": image_statuses,
                }
            )
            return change_status
        except urllib.error.HTTPError as error:
            if error.code in {403, 404}:
                self.storage.append_manifest(
                    {
                        "timestamp": utc_now(),
                        "source": self.adapter.source_key,
                        "source_object_id": source_id,
                        "source_api_url": source_api_url,
                        "status": "filtered_source_unavailable",
                        "http_status": error.code,
                        "error": str(error),
                    }
                )
                return "filtered"
            self.storage.append_manifest(
                {
                    "timestamp": utc_now(),
                    "source": self.adapter.source_key,
                    "source_object_id": source_id,
                    "source_api_url": source_api_url,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            raise
        except Exception as error:
            self.storage.append_manifest(
                {
                    "timestamp": utc_now(),
                    "source": self.adapter.source_key,
                    "source_object_id": source_id,
                    "source_api_url": source_api_url,
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
            raise

    def crawl_many(
        self,
        source_ids: Iterable[str],
        *,
        max_new: int,
        on_progress: ProgressCallback | None = None,
    ) -> CrawlSummary:
        """Process at most max_new incomplete objects; completed objects do not consume it."""
        ids = list(source_ids)
        completed = 0
        created = 0
        updated = 0
        unchanged = 0
        skipped = 0
        filtered = 0
        failed = 0
        attempted = 0
        examined = 0
        limit_reached = False

        for position, source_id in enumerate(ids, 1):
            examined = position
            if self.is_complete(source_id):
                skipped += 1
                if on_progress is not None:
                    on_progress(position, len(ids), source_id, "skipped", None)
                continue
            if attempted >= max_new:
                limit_reached = True
                examined -= 1
                break

            attempted += 1
            try:
                result = self.crawl_one(source_id)
                if result == "skipped":
                    skipped += 1
                    attempted -= 1
                elif result == "filtered":
                    filtered += 1
                    attempted -= 1
                else:
                    completed += 1
                    if result == "created":
                        created += 1
                    elif result == "updated":
                        updated += 1
                    elif result == "unchanged":
                        unchanged += 1
                if on_progress is not None:
                    on_progress(position, len(ids), source_id, result, None)
            except Exception as error:
                failed += 1
                if on_progress is not None:
                    on_progress(position, len(ids), source_id, "failed", error)

        return CrawlSummary(
            discovered=len(ids),
            examined=examined,
            attempted=attempted,
            completed=completed,
            created=created,
            updated=updated,
            unchanged=unchanged,
            skipped=skipped,
            filtered=filtered,
            failed=failed,
            limit_reached=limit_reached,
        )
