import json
import tempfile
import unittest
from pathlib import Path

from nexo_crawler.storage import DatasetStorage, image_extension


class StorageTests(unittest.TestCase):
    def temporary_directory(self):
        return tempfile.TemporaryDirectory(dir=Path(__file__).parent)

    def test_image_extension_prefers_content_type(self):
        self.assertEqual(image_extension("image/png", "https://example.test/image.jpg"), ".png")
        self.assertEqual(image_extension(None, "https://example.test/image.jpeg?x=1"), ".jpg")
        self.assertEqual(image_extension(None, "https://example.test/no-extension"), ".img")

    def test_rebuild_metadata_combines_source_directories_in_record_order(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("source-b")
            storage.prepare("source-a")
            storage.write_json(
                storage.record_path("source-b", "record-2"),
                {"record_id": "source-b:2:primary"},
            )
            storage.write_json(
                storage.record_path("source-a", "record-1"),
                {"record_id": "source-a:1:primary"},
            )

            self.assertEqual(storage.rebuild_metadata(), 2)
            rows = [
                json.loads(line)
                for line in storage.metadata_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [row["record_id"] for row in rows],
                ["source-a:1:primary", "source-b:2:primary"],
            )

    def test_resume_check_requires_downloaded_image_file(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            record_path = storage.record_path("example", "one")
            storage.write_json(
                record_path,
                {
                    "image": {
                        "status": "downloaded",
                        "local_path": "images/example/one.jpg",
                    }
                },
            )
            self.assertFalse(storage.record_is_complete(record_path, skip_images=False))
            image_path = storage.output_dir / "images" / "example" / "one.jpg"
            image_path.write_bytes(b"image")
            self.assertTrue(storage.record_is_complete(record_path, skip_images=False))

    def test_discovery_snapshot_preserves_selector_and_complete_id_list(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            path = storage.write_discovery(
                "example",
                {
                    "method": "all",
                    "parameters": {"department_ids": [5]},
                    "request_url": "https://api.example.test/objects?departmentIds=5",
                    "total_reported": 2,
                    "discovered_count": 2,
                    "source_ids": ["one", "two"],
                },
            )
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["source_ids"], ["one", "two"])
            self.assertEqual(snapshot["parameters"]["department_ids"], [5])
            self.assertEqual(snapshot["discovered_count"], 2)

    def test_refresh_job_resumes_until_marked_complete(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            selector = {"method": "all", "parameters": {"updated_since": "2026-01-01"}}

            path, first, resumed = storage.start_or_resume_refresh("example", selector)
            self.assertFalse(resumed)
            same_path, second, resumed = storage.start_or_resume_refresh("example", selector)
            self.assertTrue(resumed)
            self.assertEqual(path, same_path)
            self.assertEqual(first["job_id"], second["job_id"])

            storage.update_refresh_job(path, completed=True, summary={"completed": 2})
            _, third, resumed = storage.start_or_resume_refresh("example", selector)
            self.assertFalse(resumed)
            self.assertNotEqual(first["job_id"], third["job_id"])

    def test_refresh_completion_requires_record_checked_after_job_start(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            record_path = storage.record_path("example", "one")
            storage.write_json(
                record_path,
                {
                    "source": {"retrieved_at": "2026-01-01T00:00:00Z"},
                    "image": {"status": "no_image", "local_path": None},
                },
            )
            self.assertFalse(
                storage.record_is_complete(
                    record_path,
                    skip_images=False,
                    checked_after="2026-02-01T00:00:00Z",
                )
            )
            self.assertTrue(
                storage.record_is_complete(
                    record_path,
                    skip_images=False,
                    checked_after="2025-12-01T00:00:00Z",
                )
            )


if __name__ == "__main__":
    unittest.main()
