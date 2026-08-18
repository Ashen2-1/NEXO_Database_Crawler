"""Adapter for the Metropolitan Museum of Art Collection API."""

from __future__ import annotations

import argparse
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

from ..enrichers import fetch_wikidata_description
from ..http import HttpClient
from ..models import (
    CanonicalRecord,
    CreationDateInfo,
    CreatorInfo,
    RightsInfo,
    SourceInfo,
)
from .base import (
    DiscoveryResult,
    ImageCandidate,
    NormalizationContext,
    RecordIdentity,
    SourceAdapter,
    ordered_unique,
)


API_BASE = "https://collectionapi.metmuseum.org/public/collection/v1"

TARGET_SEARCHES: dict[str, list[dict[str, str]]] = {
    "person": [
        {"q": "Portraits", "hasImages": "true", "isHighlight": "true"},
        {"q": "Men", "hasImages": "true", "isHighlight": "true"},
        {"q": "Women", "hasImages": "true", "isHighlight": "true"},
        {"q": "Portraits", "hasImages": "true"},
    ],
    "architecture": [
        {"q": "Architecture", "hasImages": "true"},
    ],
    "painting": [
        {"q": "painting", "medium": "Paintings", "hasImages": "true", "isHighlight": "true"},
        {"q": "painting", "medium": "Paintings", "hasImages": "true"},
    ],
}


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


def _iso_date(value: str) -> str:
    """Validate an ISO calendar date for the Met metadataDate filter."""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD format") from error


def _optional_creator(payload: dict[str, Any]) -> list[CreatorInfo] | None:
    """Map Met artist fields to CreatorInfo, or return None if all fields are empty."""
    creator = CreatorInfo(
        name=payload.get("artistDisplayName") or None,
        role=payload.get("artistRole") or None,
        attribution=payload.get("artistPrefix") or None,
        suffix=payload.get("artistSuffix") or None,
        sort_name=payload.get("artistAlphaSort") or None,
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


def _target_flags(payload: dict[str, Any]) -> dict[str, bool]:
    """Classify supported demo targets from source-provided tags and object fields."""
    tags = {value.casefold() for value in (_optional_tags(payload) or [])}
    classification = str(payload.get("classification") or "").casefold()
    object_name = str(payload.get("objectName") or "").casefold()
    title = str(payload.get("title") or "").casefold()
    person_tags = {
        "boys",
        "children",
        "girls",
        "human figures",
        "men",
        "people",
        "portraits",
        "women",
    }
    return {
        "target_person": bool(tags & person_tags)
        or "portrait" in classification
        or "portrait" in object_name
        or "portrait" in title,
        "target_architecture": bool(tags & {"architecture", "buildings"})
        or "architect" in classification
        or "architect" in object_name,
        "target_painting": "painting" in classification or "painting" in object_name,
    }


class MetMuseumAdapter(SourceAdapter):
    source_key = "metmuseum"
    source_name = "The Metropolitan Museum of Art"

    @classmethod
    def add_cli_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Register Met-specific direct, search, and bulk discovery options."""
        parser.add_argument(
            "--object-id",
            action="append",
            type=_positive_object_id,
            default=[],
            help="Met object ID; repeat for multiple objects",
        )
        parser.add_argument("--ids-file", type=Path, help="UTF-8 file with one Met object ID per line")
        parser.add_argument("--query", help="Met Collection API search query")
        parser.add_argument(
            "--target",
            action="append",
            choices=sorted(TARGET_SEARCHES),
            default=[],
            help="curated thematic discovery; repeat for multiple targets",
        )
        parser.add_argument(
            "--all",
            dest="all_objects",
            action="store_true",
            help="discover all Met object IDs; processing is still capped by --limit",
        )
        parser.add_argument(
            "--department-id",
            action="append",
            type=int,
            default=[],
            help="department filter; repeat for multiple departments in --all mode",
        )
        parser.add_argument(
            "--updated-since",
            type=_iso_date,
            help="with --all, refresh records updated after YYYY-MM-DD",
        )
        parser.add_argument(
            "--include-results-without-images",
            action="store_true",
            help="do not filter Met search results to records with images",
        )

    def discover(self, args: argparse.Namespace, client: HttpClient) -> DiscoveryResult:
        """Collect Met IDs from direct inputs, search, or the bulk objects endpoint."""
        department_ids = args.department_id or []
        targets = ordered_unique(args.target or [])
        if any(department_id <= 0 for department_id in department_ids):
            raise ValueError("--department-id values must be positive")
        has_explicit_ids = bool(args.object_id or args.ids_file is not None)
        if targets and (
            has_explicit_ids
            or args.query
            or args.all_objects
            or department_ids
            or args.updated_since
            or args.include_results_without_images
        ):
            raise ValueError(
                "--target cannot be combined with IDs, --query, --all, department, update, "
                "or image-filter overrides"
            )
        if args.all_objects and (has_explicit_ids or args.query):
            raise ValueError("--all cannot be combined with --object-id, --ids-file, or --query")
        if args.updated_since and not args.all_objects:
            raise ValueError("--updated-since requires --all")
        if department_ids and not (args.query or args.all_objects):
            raise ValueError("--department-id requires --query or --all")
        if args.query and len(department_ids) > 1:
            raise ValueError("Met search accepts only one --department-id")
        if args.include_results_without_images and not args.query:
            raise ValueError("--include-results-without-images requires --query")
        if not has_explicit_ids and not args.query and not args.all_objects and not targets:
            raise ValueError(
                "provide --object-id, --ids-file, --query, --target, or --all; "
                "the crawler never fetches the whole collection implicitly"
            )

        if targets:
            object_ids: list[str] = []
            searches: list[dict[str, Any]] = []
            for target in targets:
                for parameters in TARGET_SEARCHES[target]:
                    url = f"{API_BASE}/search?{urllib.parse.urlencode(parameters)}"
                    response = client.get_json(url)
                    values = response.get("objectIDs") or []
                    if not isinstance(values, list):
                        raise ValueError("Met target search returned an invalid objectIDs field")
                    found = [str(value) for value in values if isinstance(value, int) and value > 0]
                    object_ids.extend(found)
                    searches.append(
                        {
                            "target": target,
                            "parameters": parameters,
                            "request_url": url,
                            "total_reported": response.get("total"),
                            "discovered_count": len(found),
                        }
                    )
            source_ids = ordered_unique(object_ids)
            return DiscoveryResult(
                source_ids=source_ids,
                method="targets",
                parameters={"targets": targets, "searches": searches},
                total_reported=len(source_ids),
            )

        if args.all_objects:
            parameters: dict[str, str] = {}
            if department_ids:
                parameters["departmentIds"] = "|".join(str(value) for value in department_ids)
            if args.updated_since:
                parameters["metadataDate"] = args.updated_since
            query_string = urllib.parse.urlencode(parameters)
            url = f"{API_BASE}/objects" + (f"?{query_string}" if query_string else "")
            response = client.get_json(url)
            values = response.get("objectIDs") or []
            if not isinstance(values, list):
                raise ValueError("Met objects response contained an invalid objectIDs field")
            source_ids = ordered_unique(
                str(value) for value in values if isinstance(value, int) and value > 0
            )
            total = response.get("total")
            return DiscoveryResult(
                source_ids=source_ids,
                method="all",
                parameters={
                    "department_ids": department_ids or None,
                    "updated_since": args.updated_since,
                },
                request_url=url,
                total_reported=total if isinstance(total, int) else None,
            )

        object_ids: list[str] = list(args.object_id)
        if args.ids_file is not None:
            object_ids.extend(_read_object_ids(args.ids_file))
        request_url: str | None = None
        total_reported: int | None = None
        if args.query:
            parameters: dict[str, str | int] = {"q": args.query}
            if not args.include_results_without_images:
                parameters["hasImages"] = "true"
            if department_ids:
                parameters["departmentId"] = department_ids[0]
            request_url = f"{API_BASE}/search?{urllib.parse.urlencode(parameters)}"
            response = client.get_json(request_url)
            values = response.get("objectIDs") or []
            if not isinstance(values, list):
                raise ValueError("Met search response contained an invalid objectIDs field")
            object_ids.extend(str(value) for value in values if isinstance(value, int) and value > 0)
            total = response.get("total")
            total_reported = total if isinstance(total, int) else None
        method = "mixed" if has_explicit_ids and args.query else "query" if args.query else "ids"
        return DiscoveryResult(
            source_ids=ordered_unique(object_ids),
            method=method,
            parameters={
                "object_ids": list(args.object_id) or None,
                "ids_file": str(args.ids_file) if args.ids_file is not None else None,
                "query": args.query,
                "department_ids": department_ids or None,
                "has_images_filter": bool(args.query and not args.include_results_without_images),
            },
            request_url=request_url,
            total_reported=total_reported,
        )

    def fetch(self, source_id: str, client: HttpClient) -> dict[str, Any]:
        """Fetch one object from the Met Collection API and verify the returned ID."""
        payload = client.get_json(self.api_url(source_id))
        if str(payload.get("objectID")) != source_id:
            raise ValueError(
                f"Met returned objectID {payload.get('objectID')} for requested ID {source_id}"
            )
        return payload

    def enrich(
        self,
        source_id: str,
        raw: dict[str, Any],
        client: HttpClient,
    ) -> dict[str, Any]:
        """Fetch the Met object's own Wikidata short description when an entity ID exists."""
        return fetch_wikidata_description(raw.get("objectWikidata_URL"), client)

    def matches_targets(self, raw: dict[str, Any], targets: tuple[str, ...]) -> bool:
        """Accept a candidate only when Met's own fields match a requested target."""
        if not targets:
            return True
        flags = _target_flags(raw)
        return any(flags[f"target_{target}"] for target in targets)

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
        enrichment = context.enrichment if context.enrichment_requested else {}
        description_status = str(
            enrichment.get("description_status")
            or ("no_source" if context.enrichment_requested else "not_requested")
        )
        targets = _target_flags(raw)
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
            description=enrichment.get("description") or None,
            description_status=description_status,
            description_source=enrichment.get("description_source") or None,
            description_source_url=enrichment.get("description_source_url") or None,
            description_language=enrichment.get("description_language") or None,
            object_type=raw.get("objectName") or None,
            category=raw.get("classification") or None,
            classification=raw.get("classification") or None,
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
            dynasty=raw.get("dynasty") or None,
            reign=raw.get("reign") or None,
            portfolio=raw.get("portfolio") or None,
            country=raw.get("country") or None,
            region=raw.get("region") or None,
            subregion=raw.get("subregion") or None,
            locale=raw.get("locale") or None,
            city=raw.get("city") or None,
            state=raw.get("state") or None,
            county=raw.get("county") or None,
            geography_type=raw.get("geographyType") or None,
            locus=raw.get("locus") or None,
            excavation=raw.get("excavation") or None,
            river=raw.get("river") or None,
            brand=None,
            model=None,
            catalog_number=raw.get("accessionNumber") or None,
            accession_year=raw.get("accessionYear") or None,
            department=raw.get("department") or None,
            repository=raw.get("repository") or None,
            object_wikidata_url=raw.get("objectWikidata_URL") or None,
            gallery_number=raw.get("GalleryNumber") or None,
            is_highlight=raw.get("isHighlight"),
            is_timeline_work=raw.get("isTimelineWork"),
            link_resource=raw.get("linkResource") or None,
            target_person=targets["target_person"],
            target_architecture=targets["target_architecture"],
            target_painting=targets["target_painting"],
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
                "artist_suffix": raw.get("artistSuffix"),
                "artist_alpha_sort": raw.get("artistAlphaSort"),
                "geography_type": raw.get("geographyType"),
                "locus": raw.get("locus"),
                "excavation": raw.get("excavation"),
                "river": raw.get("river"),
                "object_wikidata_url": raw.get("objectWikidata_URL"),
                "gallery_number": raw.get("GalleryNumber"),
                "is_highlight": raw.get("isHighlight"),
                "is_timeline_work": raw.get("isTimelineWork"),
                "link_resource": raw.get("linkResource"),
                "tag_details": raw.get("tags"),
                "wikidata_entity_id": enrichment.get("wikidata_entity_id"),
                "wikidata_label": enrichment.get("wikidata_label"),
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
