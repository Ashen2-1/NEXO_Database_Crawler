import json
import tempfile
import unittest
from pathlib import Path

from crawler import (
    image_extension,
    normalize_record,
    ordered_unique,
    rebuild_metadata,
    record_is_complete,
    search_object_ids,
)


class FakeClient:
    def __init__(self):
        self.url = None

    def get_json(self, url):
        self.url = url
        return {"objectIDs": [4, 7]}


class CrawlerTests(unittest.TestCase):
    def temporary_directory(self):
        return tempfile.TemporaryDirectory(dir=Path(__file__).parent)

    def test_ordered_unique_preserves_first_occurrence(self):
        self.assertEqual(ordered_unique([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_image_extension_prefers_content_type(self):
        self.assertEqual(image_extension("image/png", "https://example.test/image.jpg"), ".png")
        self.assertEqual(image_extension(None, "https://example.test/image.jpeg?x=1"), ".jpg")
        self.assertEqual(image_extension(None, "https://example.test/no-extension"), ".img")

    def test_search_only_adds_has_images_filter_when_requested(self):
        client = FakeClient()
        self.assertEqual(search_object_ids(client, "blue vase", 5, True), [4, 7])
        self.assertIn("hasImages=true", client.url)
        self.assertIn("departmentId=5", client.url)

        search_object_ids(client, "blue vase", None, False)
        self.assertNotIn("hasImages", client.url)

    def test_normalize_record_uses_only_source_values(self):
        payload = {
            "objectID": 42,
            "objectURL": "https://www.metmuseum.org/art/collection/search/42",
            "isPublicDomain": True,
            "primaryImage": "https://images.example.test/42.jpg",
            "additionalImages": [],
            "title": "Source title",
            "objectName": "Photograph",
            "objectDate": "1901",
            "objectBeginDate": 1901,
            "objectEndDate": 1901,
            "medium": "Albumen silver print",
            "dimensions": "10 x 20 cm",
            "department": "Photographs",
            "classification": "Photographs",
            "artistDisplayName": "Source Artist",
            "country": "United States",
            "tags": [{"term": "Trees"}],
        }
        record = normalize_record(
            payload,
            fetched_at="2026-01-01T00:00:00Z",
            raw_source_path="raw/MET-42.json",
            image_info={
                "status": "downloaded",
                "local_path": "images/MET-42.jpg",
                "sha256": "abc",
                "bytes": 123,
                "content_type": "image/jpeg",
            },
        )
        self.assertEqual(record["title"], "Source title")
        self.assertEqual(record["creator"]["display_name"], "Source Artist")
        self.assertEqual(record["geography"]["country"], "United States")
        self.assertEqual(record["annotation"]["status"], "source_only")
        self.assertEqual(record["annotation"]["method"], "none")
        self.assertNotIn("description", record)

    def test_rebuild_metadata_sorts_and_deduplicates_by_record_file(self):
        with self.temporary_directory() as temporary_directory:
            root = Path(temporary_directory)
            records = root / "records"
            records.mkdir()
            (records / "MET-9.json").write_text(
                json.dumps({"record_id": "MET-9", "source_object_id": 9}), encoding="utf-8"
            )
            (records / "MET-2.json").write_text(
                json.dumps({"record_id": "MET-2", "source_object_id": 2}), encoding="utf-8"
            )
            output = root / "metadata.jsonl"
            self.assertEqual(rebuild_metadata(records, output), 2)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["source_object_id"] for row in rows], [2, 9])

    def test_record_is_complete_checks_downloaded_image(self):
        with self.temporary_directory() as temporary_directory:
            root = Path(temporary_directory)
            record_path = root / "records" / "MET-1.json"
            record_path.parent.mkdir()
            record_path.write_text(
                json.dumps({"image_status": "downloaded", "image": "images/MET-1.jpg"}),
                encoding="utf-8",
            )
            self.assertFalse(record_is_complete(record_path, root, skip_images=False))
            image_path = root / "images" / "MET-1.jpg"
            image_path.parent.mkdir()
            image_path.write_bytes(b"image")
            self.assertTrue(record_is_complete(record_path, root, skip_images=False))


if __name__ == "__main__":
    unittest.main()
