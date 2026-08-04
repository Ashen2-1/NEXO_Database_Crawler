"""Adapter interface implemented for each external website or API."""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable

from ..http import HttpClient
from ..models import CanonicalRecord, ImageInfo


@dataclass(frozen=True)
class ImageCandidate:
    key: str
    role: str
    source_url: str | None
    download_allowed: bool
    blocked_status: str = "not_downloadable"


@dataclass(frozen=True)
class RecordIdentity:
    record_id: str
    file_stem: str


@dataclass(frozen=True)
class NormalizationContext:
    identity: RecordIdentity
    retrieved_at: str
    raw_path: str
    image: ImageInfo


@dataclass(frozen=True)
class DiscoveryResult:
    source_ids: list[str]
    method: str
    parameters: dict[str, Any]
    request_url: str | None = None
    total_reported: int | None = None


def ordered_unique(values: Iterable[str]) -> list[str]:
    """Return values in first-seen order with duplicates removed."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


class SourceAdapter(ABC):
    source_key: str
    source_name: str

    @classmethod
    @abstractmethod
    def add_cli_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Register source-specific discovery arguments."""

    @abstractmethod
    def discover(self, args: argparse.Namespace, client: HttpClient) -> DiscoveryResult:
        """Return identifiers and provenance for one discovery operation."""

    @abstractmethod
    def fetch(self, source_id: str, client: HttpClient) -> dict[str, Any]:
        """Fetch one source object as raw structured data."""

    @abstractmethod
    def raw_file_stem(self, source_id: str) -> str:
        """Return a filesystem-safe name for the raw source object."""

    @abstractmethod
    def image_candidates(self, source_id: str, raw: dict[str, Any]) -> list[ImageCandidate]:
        """Return the image samples associated with the source object."""

    @abstractmethod
    def identity(self, source_id: str, image: ImageCandidate) -> RecordIdentity:
        """Return stable logical and filesystem identifiers for an image sample."""

    @abstractmethod
    def normalize(
        self,
        source_id: str,
        raw: dict[str, Any],
        context: NormalizationContext,
    ) -> CanonicalRecord:
        """Map raw source fields to the canonical schema without inference."""

    @abstractmethod
    def api_url(self, source_id: str) -> str | None:
        """Return the structured-data endpoint, when one exists."""

    @abstractmethod
    def page_url(self, source_id: str, raw: dict[str, Any]) -> str | None:
        """Return the human-readable source page, when one exists."""

    def display_reference(self, source_id: str) -> str:
        """Return a short label for progress output (default: source_key:source_id)."""
        return f"{self.source_key}:{source_id}"
