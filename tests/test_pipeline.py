import argparse
import json
import tempfile
import unittest
from pathlib import Path

from nexo_crawler.http import HttpResponse
from nexo_crawler.models import CanonicalRecord, SourceInfo
from nexo_crawler.pipeline import CrawlPipeline
from nexo_crawler.sources.base import (
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

    @classmethod
    def add_cli_arguments(cls, parser: argparse.ArgumentParser) -> None:
        pass

    def discover(self, args, client):
        return ["one"]

    def fetch(self, source_id, client):
        self.fetches += 1
        return {"id": source_id, "title": "From source"}

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

            self.assertEqual(pipeline.crawl_one("one"), "completed")
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


if __name__ == "__main__":
    unittest.main()
