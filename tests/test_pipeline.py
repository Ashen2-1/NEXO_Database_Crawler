import argparse
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from nexo_crawler.http import HttpAccessBlockedError, HttpResponse
from nexo_crawler.models import CanonicalRecord, CreationDateInfo, CreatorInfo, SourceInfo
from nexo_crawler.pipeline import CrawlPipeline, utc_now
from nexo_crawler.sources.base import (
    DiscoveryResult,
    ImageCandidate,
    NormalizationContext,
    RecordIdentity,
    SourceAdapter,
)
from nexo_crawler.storage import DatasetStorage


class FakeClient:
    def __init__(self):
        self.image_requests = 0

    def get(self, url):
        self.image_requests += 1
        return HttpResponse(data=b"fake-image", content_type="image/jpeg", final_url=url)


class FakeAdapter(SourceAdapter):
    source_key = "example"
    source_name = "Example Source"

    def __init__(self):
        self.fetches = 0
        self.title = "From source"

    @classmethod
    def add_cli_arguments(cls, parser: argparse.ArgumentParser) -> None:
        pass

    def discover(self, args, client):
        return DiscoveryResult(source_ids=["one"], method="ids", parameters={})

    def fetch(self, source_id, client):
        self.fetches += 1
        return {"id": source_id, "title": self.title}

    def raw_file_stem(self, source_id):
        return source_id

    def image_candidates(self, source_id, raw):
        return [
            ImageCandidate(
                key="primary",
                role="primary",
                source_url="https://example.test/image.jpg",
                download_allowed=True,
            )
        ]

    def identity(self, source_id, image):
        return RecordIdentity(f"example:{source_id}:{image.key}", f"{source_id}-{image.key}")

    def normalize(self, source_id, raw, context: NormalizationContext):
        return CanonicalRecord(
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
            title=raw.get("title"),
            description=context.enrichment.get("description"),
            description_status=(
                context.enrichment.get("description_status", "no_source")
                if context.enrichment_requested
                else "not_requested"
            ),
            description_source=context.enrichment.get("description_source"),
            description_source_url=context.enrichment.get("description_source_url"),
            description_language=context.enrichment.get("description_language"),
            creators=([CreatorInfo(name=raw["creator_name"])] if raw.get("creator_name") else None),
            creation_date=CreationDateInfo(
                start_year=raw.get("start_year"),
                end_year=raw.get("end_year"),
            ),
        )

    def api_url(self, source_id):
        return f"https://api.example.test/{source_id}"

    def page_url(self, source_id, raw):
        return f"https://example.test/{source_id}"


class PipelineTests(unittest.TestCase):
    def temporary_directory(self):
        return tempfile.TemporaryDirectory(dir=Path(__file__).parent)

    def test_pipeline_is_source_independent_and_resumable(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            client = FakeClient()
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
            )

            self.assertEqual(pipeline.crawl_one("one"), "created")
            self.assertEqual(adapter.fetches, 1)
            self.assertEqual(client.image_requests, 1)
            self.assertTrue((storage.raw_dir / "example" / "one.json").is_file())
            self.assertTrue(
                (storage.images_dir / "example" / "one-primary.jpg").is_file()
            )

            record_path = storage.records_dir / "example" / "one-primary.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["schema_version"], "2.2")
            self.assertEqual(record["title"], "From source")
            self.assertIsNone(record["description"])

            self.assertEqual(pipeline.crawl_one("one"), "skipped")
            self.assertEqual(adapter.fetches, 1)
            self.assertEqual(client.image_requests, 1)

    def test_batch_limit_counts_only_incomplete_objects(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            client = FakeClient()
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
            )
            pipeline.crawl_one("one")

            first_batch = pipeline.crawl_many(["one", "two", "three"], max_new=1)
            self.assertEqual(first_batch.skipped, 1)
            self.assertEqual(first_batch.completed, 1)
            self.assertEqual(first_batch.attempted, 1)
            self.assertTrue(first_batch.limit_reached)
            self.assertFalse((storage.raw_dir / "example" / "three.json").exists())

            second_batch = pipeline.crawl_many(["one", "two", "three"], max_new=1)
            self.assertEqual(second_batch.skipped, 2)
            self.assertEqual(second_batch.completed, 1)
            self.assertFalse(second_batch.limit_reached)
            self.assertTrue((storage.raw_dir / "example" / "three.json").exists())

    def test_public_domain_only_filters_without_creating_a_record(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            adapter.fetch = lambda source_id, client: {
                "id": source_id,
                "title": "Copyrighted source",
                "isPublicDomain": False,
            }
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
                public_domain_only=True,
            )

            summary = pipeline.crawl_many(["one"], max_new=1)

            self.assertEqual(summary.filtered, 1)
            self.assertEqual(summary.completed, 0)
            self.assertFalse(storage.record_path("example", "one-primary").exists())

    def test_inaccessible_source_candidate_is_filtered_without_consuming_limit(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()

            def fetch(source_id, client):
                if source_id == "gone":
                    raise urllib.error.HTTPError(
                        "https://api.example.test/gone", 404, "Not Found", {}, None
                    )
                return {"id": source_id, "title": "Available"}

            adapter.fetch = fetch
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
            )

            summary = pipeline.crawl_many(["gone", "available"], max_new=1)

            self.assertEqual(summary.filtered, 1)
            self.assertEqual(summary.completed, 1)
            self.assertEqual(summary.failed, 0)

    def test_description_enrichment_is_cached_and_marks_record_complete(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            client = FakeClient()
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
                enrich_descriptions=True,
            )

            self.assertEqual(pipeline.crawl_one("one"), "created")
            record = storage.read_json(storage.record_path("example", "one-primary"))

            self.assertEqual(record["description_status"], "no_source")
            self.assertTrue(storage.enrichment_path("example", "one").exists())
            self.assertTrue(pipeline.is_complete("one"))

    def test_require_description_filters_missing_and_continues_to_limit(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()

            def enrich(source_id, raw, client):
                if source_id == "missing":
                    return {"description_status": "no_source"}
                return {
                    "description_status": "available",
                    "description": "Source-grounded description",
                    "description_source": "example",
                    "description_source_url": "https://example.test/entity/available",
                    "description_language": "en",
                }

            adapter.enrich = enrich
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
                require_description=True,
            )

            summary = pipeline.crawl_many(["missing", "available"], max_new=1)

            self.assertTrue(pipeline.enrich_descriptions)
            self.assertEqual(summary.filtered, 1)
            self.assertEqual(summary.completed, 1)
            self.assertFalse(storage.record_path("example", "missing-primary").exists())
            record = storage.read_json(storage.record_path("example", "available-primary"))
            self.assertEqual(record["description_status"], "available")

    def test_require_image_filters_missing_and_continues_to_limit(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()

            def image_candidates(source_id, raw):
                return [
                    ImageCandidate(
                        key="primary",
                        role="primary",
                        source_url=(
                            None
                            if source_id == "missing"
                            else "https://example.test/available.jpg"
                        ),
                        download_allowed=True,
                    )
                ]

            adapter.image_candidates = image_candidates
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
                require_image=True,
            )

            summary = pipeline.crawl_many(["missing", "available"], max_new=1)

            self.assertEqual(summary.filtered, 1)
            self.assertEqual(summary.completed, 1)
            self.assertFalse(storage.record_path("example", "missing-primary").exists())
            record = storage.read_json(storage.record_path("example", "available-primary"))
            self.assertEqual(record["image"]["status"], "downloaded")
            self.assertTrue(
                storage.record_is_complete(
                    storage.record_path("example", "available-primary"),
                    skip_images=False,
                    require_image=True,
                )
            )

    def test_require_image_rejects_skip_images_defensively(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                CrawlPipeline(
                    adapter=FakeAdapter(),
                    client=FakeClient(),
                    storage=storage,
                    skip_images=True,
                    force=False,
                    require_image=True,
                )

    def test_year_and_creator_filters_reject_without_consuming_limit(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()

            def fetch(source_id, client):
                values = {
                    "anonymous": {"start_year": 1900, "end_year": 1900},
                    "too-old": {
                        "creator_name": "Old Artist",
                        "start_year": 1700,
                        "end_year": 1750,
                    },
                    "accepted": {
                        "creator_name": "Accepted Artist",
                        "start_year": 1790,
                        "end_year": 1810,
                    },
                }
                return {"id": source_id, "title": source_id, **values[source_id]}

            adapter.fetch = fetch
            client = FakeClient()
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
                require_creator=True,
                year_from=1800,
                year_to=2000,
            )

            summary = pipeline.crawl_many(
                ["anonymous", "too-old", "accepted"],
                max_new=1,
            )

            self.assertEqual(summary.filtered, 2)
            self.assertEqual(summary.completed, 1)
            self.assertEqual(summary.attempted, 3)
            self.assertEqual(client.image_requests, 1)
            self.assertTrue(storage.record_path("example", "accepted-primary").exists())

    def test_failures_do_not_consume_success_limit(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()

            def fetch(source_id, client):
                if source_id == "failed":
                    raise RuntimeError("temporary source failure")
                return {"id": source_id, "title": source_id}

            adapter.fetch = fetch
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
            )

            summary = pipeline.crawl_many(
                ["failed", "one", "two", "not-examined"],
                max_new=2,
            )

            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.completed, 2)
            self.assertEqual(summary.attempted, 3)
            self.assertEqual(summary.examined, 3)
            self.assertTrue(summary.limit_reached)
            self.assertFalse(storage.raw_path("example", "not-examined").exists())

    def test_repeated_403_halts_run_instead_of_filtering_remaining_candidates(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()

            def fetch(source_id, client):
                raise HttpAccessBlockedError(f"https://api.example.test/{source_id}", 4)

            adapter.fetch = fetch
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
            )

            summary = pipeline.crawl_many(["blocked", "not-examined"], max_new=1)

            self.assertTrue(summary.halted)
            self.assertEqual(summary.failed, 1)
            self.assertEqual(summary.filtered, 0)
            self.assertEqual(summary.examined, 1)
            self.assertFalse(storage.raw_path("example", "not-examined").exists())

    def test_max_examined_stops_before_success_target(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            adapter.fetch = lambda source_id, client: {
                "id": source_id,
                "title": source_id,
                "isPublicDomain": False,
            }
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
                public_domain_only=True,
            )

            summary = pipeline.crawl_many(
                ["one", "two", "three"],
                max_new=1,
                max_examined=2,
            )

            self.assertEqual(summary.completed, 0)
            self.assertEqual(summary.filtered, 2)
            self.assertEqual(summary.examined, 2)
            self.assertTrue(summary.max_examined_reached)
            self.assertFalse(summary.limit_reached)

    def test_blocked_image_status_takes_precedence_over_missing_url(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            pipeline = CrawlPipeline(
                adapter=FakeAdapter(),
                client=FakeClient(),
                storage=storage,
                skip_images=False,
                force=False,
            )

            image = pipeline._download_image(
                "one",
                ImageCandidate(
                    key="primary",
                    role="primary",
                    source_url=None,
                    download_allowed=False,
                    blocked_status="not_public_domain",
                ),
            )

            self.assertEqual(image.status, "not_public_domain")

    def test_old_schema_is_upgraded_from_cache_without_redownloading(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            client = FakeClient()
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
            )
            self.assertEqual(pipeline.crawl_one("one"), "created")

            record_path = storage.record_path("example", "one-primary")
            old_record = storage.read_json(record_path)
            old_record["schema_version"] = "2.0"
            storage.write_json(record_path, old_record)

            self.assertEqual(pipeline.crawl_one("one"), "completed")
            self.assertEqual(adapter.fetches, 1)
            self.assertEqual(client.image_requests, 1)
            self.assertEqual(storage.read_json(record_path)["schema_version"], "2.2")

    def test_refresh_refetches_metadata_and_reuses_unchanged_image(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            client = FakeClient()
            initial = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
            )
            self.assertEqual(initial.crawl_one("one"), "created")
            self.assertEqual(client.image_requests, 1)

            refresh_started = utc_now()
            adapter.title = "Updated at source"
            refresh = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
                refresh_after=refresh_started,
            )
            self.assertEqual(refresh.crawl_one("one"), "updated")
            self.assertEqual(client.image_requests, 1)
            self.assertTrue(refresh.is_complete("one"))

            record_path = storage.records_dir / "example" / "one-primary.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["title"], "Updated at source")
            self.assertEqual(record["image"]["status"], "downloaded")

            next_refresh = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=False,
                refresh_after=utc_now(),
            )
            self.assertEqual(next_refresh.crawl_one("one"), "unchanged")
            self.assertEqual(client.image_requests, 1)

    def test_forced_refresh_uses_job_time_to_advance_between_batches(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            adapter = FakeAdapter()
            client = FakeClient()
            refresh_started = utc_now()
            pipeline = CrawlPipeline(
                adapter=adapter,
                client=client,
                storage=storage,
                skip_images=False,
                force=True,
                refresh_after=refresh_started,
            )

            first = pipeline.crawl_many(["one", "two"], max_new=1)
            self.assertEqual(first.created, 1)
            self.assertTrue(first.limit_reached)

            second = pipeline.crawl_many(["one", "two"], max_new=1)
            self.assertEqual(second.skipped, 1)
            self.assertEqual(second.created, 1)
            self.assertFalse(second.limit_reached)
            self.assertEqual(client.image_requests, 2)


if __name__ == "__main__":
    unittest.main()
