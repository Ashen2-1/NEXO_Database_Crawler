"""Canonical, source-independent dataset schema."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from . import __version__


SCHEMA_VERSION = "2.0"


@dataclass(frozen=True)
class SourceInfo:
    key: str
    name: str
    object_id: str
    api_url: str | None
    page_url: str | None
    retrieved_at: str
    raw_path: str


@dataclass(frozen=True)
class ImageInfo:
    role: str
    status: str
    source_url: str | None
    local_path: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class CreatorInfo:
    name: str | None = None
    role: str | None = None
    attribution: str | None = None
    biography: str | None = None
    nationality: str | None = None
    birth_year: str | None = None
    death_year: str | None = None
    gender: str | None = None
    ulan_url: str | None = None
    wikidata_url: str | None = None


@dataclass(frozen=True)
class CreationDateInfo:
    display: str | None = None
    start_year: int | None = None
    end_year: int | None = None


@dataclass(frozen=True)
class RightsInfo:
    public_domain: bool | None = None
    rights_text: str | None = None
    credit_line: str | None = None


@dataclass(frozen=True)
class AnnotationInfo:
    status: str = "source_only"
    method: str = "none"
    reviewed: bool = False
    note: str = "No AI-generated or human-inferred annotations were added."


@dataclass(frozen=True)
class CanonicalRecord:
    record_id: str
    source: SourceInfo
    image: ImageInfo
    title: str | None = None
    description: str | None = None
    object_type: str | None = None
    category: str | None = None
    creators: list[CreatorInfo] | None = None
    creation_date: CreationDateInfo = field(default_factory=CreationDateInfo)
    material: str | None = None
    dimensions: str | None = None
    culture: str | None = None
    period: str | None = None
    country: str | None = None
    brand: str | None = None
    model: str | None = None
    catalog_number: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    rights: RightsInfo = field(default_factory=RightsInfo)
    annotation: AnnotationInfo = field(default_factory=AnnotationInfo)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    crawler_version: str = __version__
    schema_version: str = SCHEMA_VERSION

    def validate(self) -> None:
        """Raise ValueError if required fields or downloaded-image metadata are invalid."""
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {self.schema_version}")
        if not self.record_id:
            raise ValueError("record_id cannot be empty")
        if not self.source.key or not self.source.object_id:
            raise ValueError("source key and object_id are required")
        if not self.source.raw_path:
            raise ValueError("source raw_path is required")
        if not self.image.role or not self.image.status:
            raise ValueError("image role and status are required")
        if self.image.status == "downloaded":
            if not self.image.local_path or not self.image.sha256 or self.image.bytes is None:
                raise ValueError("a downloaded image requires path, hash, and byte count")

    def to_dict(self) -> dict[str, Any]:
        """Validate the record and return a JSON-serializable dictionary."""
        self.validate()
        return asdict(self)
