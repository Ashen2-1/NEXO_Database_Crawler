"""Source-independent crawl orchestration."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .http import HttpClient
from .models import ImageInfo
from .sources.base import ImageCandidate, NormalizationContext, SourceAdapter
from .storage import DatasetStorage, atomic_write_bytes, image_extension


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        self.adapter = adapter
        self.client = client
        self.storage = storage
        self.skip_images = skip_images
        self.force = force

    def _record_is_complete(self, source_id: str, candidate: ImageCandidate) -> bool:
        identity = self.adapter.identity(source_id, candidate)
        record_path = self.storage.record_path(self.adapter.source_key, identity.file_stem)
        return self.storage.record_is_complete(record_path, self.skip_images)

    def _download_image(
        self,
        source_id: str,
        candidate: ImageCandidate,
    ) -> ImageInfo:
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
