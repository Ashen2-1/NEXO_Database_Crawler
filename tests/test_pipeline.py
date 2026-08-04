import argparse
import json
import tempfile
import unittest
from pathlib import Path

from nexo_crawler.http import HttpResponse
from nexo_crawler.models import CanonicalRecord, SourceInfo
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
            self.assertEqual(record["schema_version"], "2.0")
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
