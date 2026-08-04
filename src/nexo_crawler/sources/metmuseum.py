"""Adapter for the Metropolitan Museum of Art Collection API."""

from __future__ import annotations

import argparse
import urllib.parse
from pathlib import Path
from typing import Any

from ..http import HttpClient
from ..models import (
    CanonicalRecord,
    CreationDateInfo,
    CreatorInfo,
    RightsInfo,
    SourceInfo,
)
from .base import (
    ImageCandidate,
    NormalizationContext,
    RecordIdentity,
    SourceAdapter,
    ordered_unique,
)


API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"


def _positive_object_id(value: str) -> str:
    """Parse and validate a positive numeric Met object ID for argparse."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a numeric Met object ID") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive Met object ID")
    return str(parsed)


def _read_object_ids(path: Path) -> list[str]:
    """Read Met object IDs from a UTF-8 file, ignoring blank lines and comments."""
    object_ids: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            object_ids.append(_positive_object_id(line))
        except argparse.ArgumentTypeError as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    return object_ids


def _optional_creator(payload: dict[str, Any]) -> list[CreatorInfo] | None:
    """Map Met artist fields to CreatorInfo, or return None if all fields are empty."""
    creator = CreatorInfo(
        name=payload.get("artistDisplayName") or None,
        role=payload.get("artistRole") or None,
        attribution=payload.get("artistPrefix") or None,
        biography=payload.get("artistDisplayBio") or None,
        nationality=payload.get("artistNationality") or None,
        birth_year=payload.get("artistBeginDate") or None,
        death_year=payload.get("artistEndDate") or None,
        gender=payload.get("artistGender") or None,
        ulan_url=payload.get("artistULAN_URL") or None,
        wikidata_url=payload.get("artistWikidata_URL") or None,
    )
    return [creator] if any(creator.__dict__.values()) else None


def _optional_tags(payload: dict[str, Any]) -> list[str] | None:
    """Extract tag terms from the Met tags array, or return None if absent."""
    raw_tags = payload.get("tags")
    if raw_tags is None:
        return None
    if not isinstance(raw_tags, list):
        return None
    return [tag["term"] for tag in raw_tags if isinstance(tag, dict) and isinstance(tag.get("term"), str)]


class MetMuseumAdapter(SourceAdapter):
    source_key = "metmuseum"
    source_name = "The Metropolitan Museum of Art"

    @classmethod
    def add_cli_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Register Met-specific discovery options (object IDs, file, search query)."""
        parser.add_argument(
            "--object-id",
            action="append",
            type=_positive_object_id,
            default=[],
            help="Met object ID; repeat for multiple objects",
        )
        parser.add_argument("--ids-file", type=Path, help="UTF-8 file with one Met object ID per line")
        parser.add_argument("--query", help="Met Collection API search query")
        parser.add_argument("--department-id", type=int, help="optional Met department ID for --query")
        parser.add_argument(
            "--include-results-without-images",
            action="store_true",
            help="do not filter Met search results to records with images",
        )

    def discover(self, args: argparse.Namespace, client: HttpClient) -> list[str]:
        """Collect Met object IDs from CLI args, an IDs file, or a Collection API search."""
        if args.department_id is not None and not args.query:
            raise ValueError("--department-id requires --query")
        if args.department_id is not None and args.department_id <= 0:
            raise ValueError("--department-id must be positive")
        if not args.object_id and args.ids_file is None and not args.query:
            raise ValueError(
                "provide --object-id, --ids-file, or --query; "
                "the crawler never fetches the whole collection implicitly"
            )

        object_ids: list[str] = list(args.object_id)
        if args.ids_file is not None:
            object_ids.extend(_read_object_ids(args.ids_file))
        if args.query:
            parameters: dict[str, str | int] = {"q": args.query}
            if not args.include_results_without_images:
                parameters["hasImages"] = "true"
            if args.department_id is not None:
                parameters["departmentId"] = args.department_id
            url = f"{API_BASE}/search?{urllib.parse.urlencode(parameters)}"
            response = client.get_json(url)
            values = response.get("objectIDs") or []
            if not isinstance(values, list):
                raise ValueError("Met search response contained an invalid objectIDs field")
            object_ids.extend(str(value) for value in values if isinstance(value, int) and value > 0)
        return ordered_unique(object_ids)

    def fetch(self, source_id: str, client: HttpClient) -> dict[str, Any]:
        """Fetch one object from the Met Collection API and verify the returned ID."""
        payload = client.get_json(self.api_url(source_id))
        if str(payload.get("objectID")) != source_id:
            raise ValueError(
                f"Met returned objectID {payload.get('objectID')} for requested ID {source_id}"
            )
        return payload

    def raw_file_stem(self, source_id: str) -> str:
        """Return the filesystem stem for a Met object's raw JSON file."""
        return f"MET-{source_id}"

    def image_candidates(self, source_id: str, raw: dict[str, Any]) -> list[ImageCandidate]:
        """Return the primary image candidate, gated on Met public-domain status."""
        return [
            ImageCandidate(
                key="primary",
                role="primary",
                source_url=raw.get("primaryImage") or None,
                download_allowed=raw.get("isPublicDomain") is True,
                blocked_status="not_public_domain",
            )
        ]

    def identity(self, source_id: str, image: ImageCandidate) -> RecordIdentity:
        """Return stable record and file identifiers for a Met image sample."""
        return RecordIdentity(
            record_id=f"metmuseum:{source_id}:{image.key}",
            file_stem=f"MET-{source_id}-{image.key}",
        )

    def normalize(
        self,
        source_id: str,
        raw: dict[str, Any],
        context: NormalizationContext,
    ) -> CanonicalRecord:
        """Map Met API fields to the canonical schema without inference."""
        record = CanonicalRecord(
            record_id=context.identity.record_id,
            source=SourceInfo(
                key=self.source_key,
                name=self.source_name,
                object_id=source_id,
                api_url=self.api_url(source_id),
                page_url=self.page_url(source_id, raw),
                retrieved_at=context.retrieved_at,
                raw_path=context.raw_path,
            ),
            image=context.image,
            title=raw.get("title") or None,
            description=None,
            object_type=raw.get("objectName") or None,
            category=raw.get("classification") or None,
            creators=_optional_creator(raw),
            creation_date=CreationDateInfo(
                display=raw.get("objectDate") or None,
                start_year=raw.get("objectBeginDate"),
                end_year=raw.get("objectEndDate"),
            ),
            material=raw.get("medium") or None,
            dimensions=raw.get("dimensions") or None,
            culture=raw.get("culture") or None,
            period=raw.get("period") or None,
            country=raw.get("country") or None,
            brand=None,
            model=None,
            catalog_number=raw.get("accessionNumber") or None,
            department=raw.get("department") or None,
            tags=_optional_tags(raw),
            rights=RightsInfo(
                public_domain=raw.get("isPublicDomain"),
                rights_text=raw.get("rightsAndReproduction") or None,
                credit_line=raw.get("creditLine") or None,
            ),
            source_metadata={
                "accession_year": raw.get("accessionYear"),
                "additional_image_urls": raw.get("additionalImages"),
                "constituents": raw.get("constituents"),
                "dynasty": raw.get("dynasty"),
                "reign": raw.get("reign"),
                "portfolio": raw.get("portfolio"),
                "repository": raw.get("repository"),
                "region": raw.get("region"),
                "subregion": raw.get("subregion"),
                "locale": raw.get("locale"),
                "city": raw.get("city"),
                "state": raw.get("state"),
                "county": raw.get("county"),
                "measurements": raw.get("measurements"),
                "metadata_date": raw.get("metadataDate"),
                "primary_image_small_url": raw.get("primaryImageSmall"),
            },
        )
        record.validate()
        return record

    def api_url(self, source_id: str) -> str:
        """Return the Met Collection API object endpoint for the given ID."""
        return f"{API_BASE}/objects/{source_id}"

    def page_url(self, source_id: str, raw: dict[str, Any]) -> str | None:
        """Return the Met museum web page URL from the API payload."""
        return raw.get("objectURL") or None

    def display_reference(self, source_id: str) -> str:
        """Return a Met-prefixed label for progress output."""
        return f"MET-{source_id}"
