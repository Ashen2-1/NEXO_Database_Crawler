"""Source-independent crawling orchestration."""

from __future__ import annotations

import hashlib
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
    skipped: int
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
    ) -> None:
        """Wire up the adapter, HTTP client, storage layer, and crawl flags."""
        self.adapter = adapter
        self.client = client
        self.storage = storage
        self.skip_images = skip_images
        self.force = force

    def _record_is_complete(self, source_id: str, candidate: ImageCandidate) -> bool:
        """Return whether the canonical record for this image candidate already exists."""
        identity = self.adapter.identity(source_id, candidate)
        record_path = self.storage.record_path(self.adapter.source_key, identity.file_stem)
        return self.storage.record_is_complete(record_path, self.skip_images)

    def is_complete(self, source_id: str) -> bool:
        """Return whether all image records for a cached source object are complete."""
        if self.force:
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

    def _download_image(
        self,
        source_id: str,
        candidate: ImageCandidate,
    ) -> ImageInfo:
        """Download or skip an image candidate and return its ImageInfo status."""
        if not candidate.source_url:
            return ImageInfo(role=candidate.role, status="no_image", source_url=None)
        if not candidate.download_allowed:
            return ImageInfo(
                role=candidate.role,
                status=candidate.blocked_status,
                source_url=candidate.source_url,
            )
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

    def crawl_one(self, source_id: str) -> str:
        """Fetch, normalize, and persist one source object; return 'skipped' or 'completed'."""
        raw_path = self.storage.raw_path(
            self.adapter.source_key,
            self.adapter.raw_file_stem(source_id),
        )
        source_api_url = self.adapter.api_url(source_id)
        try:
            if raw_path.exists() and not self.force:
                raw = self.storage.read_json(raw_path)
                retrieved_at = self.storage.raw_file_timestamp(raw_path)
                candidates = self.adapter.image_candidates(source_id, raw)
                if candidates and all(
                    self._record_is_complete(source_id, candidate) for candidate in candidates
                ):
                    self.storage.append_manifest(
                        {
                            "timestamp": utc_now(),
                            "source": self.adapter.source_key,
                            "source_object_id": source_id,
                            "status": "skipped_existing",
                        }
                    )
                    return "skipped"
            else:
                raw = self.adapter.fetch(source_id, self.client)
                retrieved_at = utc_now()
                self.storage.write_json(raw_path, raw)
                candidates = self.adapter.image_candidates(source_id, raw)

            if not candidates:
                raise ValueError(f"{self.adapter.display_reference(source_id)} produced no image samples")

            image_statuses: list[str] = []
            for candidate in candidates:
                identity = self.adapter.identity(source_id, candidate)
                record_path = self.storage.record_path(self.adapter.source_key, identity.file_stem)
                if not self.force and self.storage.record_is_complete(record_path, self.skip_images):
                    existing = self.storage.read_json(record_path)
                    existing_image = existing.get("image")
                    if isinstance(existing_image, dict) and isinstance(existing_image.get("status"), str):
                        image_statuses.append(existing_image["status"])
                    continue

                image = self._download_image(source_id, candidate)
                context = NormalizationContext(
                    identity=identity,
                    retrieved_at=retrieved_at,
                    raw_path=self.storage.relative_path(raw_path),
                    image=image,
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
                    "image_statuses": image_statuses,
                }
            )
            return "completed"
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
        skipped = 0
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
                else:
                    completed += 1
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
            skipped=skipped,
            failed=failed,
            limit_reached=limit_reached,
        )
