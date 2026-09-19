import csv
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
            with storage.metadata_csv_path.open(encoding="utf-8", newline="") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(
                [row["record_id"] for row in csv_rows],
                ["source-a:1:primary", "source-b:2:primary"],
            )

    def test_rebuild_metadata_flattens_csv_without_nested_json_cells(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            storage.write_json(
                storage.record_path("example", "one"),
                {
                    "record_id": "example:1:primary",
                    "source": {
                        "key": "example",
                        "name": "Example Museum",
                        "object_id": "1",
                        "page_url": "https://example.test/object/1",
                    },
                    "image": {
                        "role": "primary",
                        "status": "downloaded",
                        "local_path": "images/example/one.jpg",
                        "bytes": 123,
                    },
                    "title": "Portrait, with comma",
                    "description": "First line\nSecond line",
                    "classification": "Photographs",
                    "creators": [
                        {
                            "name": "Example Artist",
                            "role": "Artist",
                            "nationality": "American",
                        }
                    ],
                    "creation_date": {"display": "1900", "start_year": 1900},
                    "country": "United States",
                    "region": "North America",
                    "city": "New York",
                    "tags": ["portrait", "photograph"],
                    "rights": {"public_domain": True},
                    "annotation": {"reviewed": False},
                    "source_metadata": {
                        "locale": None,
                        "additional_image_urls": ["https://example.test/alternate.jpg"],
                        "measurements": [{"width": 10}],
                        "tag_details": [
                            {
                                "term": "portrait",
                                "AAT_URL": "https://vocab.example.test/portrait",
                                "Wikidata_URL": "https://wikidata.example.test/portrait",
                            }
                        ],
                        "constituents": [
                            {
                                "name": "Example Artist",
                                "role": "Artist",
                                "constituentULAN_URL": "https://ulan.example.test/artist",
                            }
                        ],
                    },
                    "schema_version": "2.2",
                },
            )

            self.assertEqual(storage.rebuild_metadata(), 1)
            with storage.metadata_csv_path.open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))

            self.assertEqual(row["image"], "images/example/one.jpg")
            self.assertEqual(row["title"], "Portrait, with comma")
            self.assertEqual(row["description"], "First line\nSecond line")
            self.assertEqual(row["creation_start_year"], "1900")
            self.assertEqual(row["classification"], "Photographs")
            self.assertEqual(row["creator_count"], "1")
            self.assertEqual(row["creator_name"], "Example Artist")
            self.assertEqual(row["creator_nationality"], "American")
            self.assertEqual(row["country"], "United States")
            self.assertEqual(row["region"], "North America")
            self.assertEqual(row["city"], "New York")
            self.assertEqual(row["rights_public_domain"], "true")
            self.assertEqual(row["annotation_reviewed"], "false")
            self.assertEqual(row["tags"], "portrait | photograph")
            self.assertEqual(row["tag_count"], "2")
            self.assertEqual(row["tag_aat_urls"], "https://vocab.example.test/portrait")
            self.assertEqual(row["additional_image_count"], "1")
            self.assertEqual(row["additional_image_urls"], "https://example.test/alternate.jpg")
            self.assertEqual(row["constituent_count"], "1")
            self.assertEqual(row["constituent_names"], "Example Artist")
            self.assertEqual(row["constituent_ulan_urls"], "https://ulan.example.test/artist")
            self.assertNotIn("creators", row)
            self.assertNotIn("measurements", row)
            self.assertNotIn("tag_details", row)
            self.assertNotIn("constituents", row)
            self.assertNotIn("source_metadata", row)
            self.assertNotIn("constituent_genders", row)

            with storage.ai_metadata_csv_path.open(encoding="utf-8", newline="") as handle:
                ai_row = next(csv.DictReader(handle))
            self.assertEqual(ai_row["creator_name"], "Example Artist")
            self.assertEqual(ai_row["description"], "First line\nSecond line")
            self.assertNotIn("source_page_url", ai_row)
            self.assertNotIn("image_source_url", ai_row)
            self.assertNotIn("description_source_url", ai_row)
            self.assertNotIn("annotation_status", ai_row)
            self.assertNotIn("annotation_method", ai_row)
            self.assertNotIn("annotation_reviewed", ai_row)
            self.assertNotIn("annotation_note", ai_row)
            self.assertFalse(any(column.endswith("_url") for column in ai_row))
            self.assertFalse(any(column.startswith("source_") for column in ai_row))
            self.assertFalse(any(column.startswith("rights_") for column in ai_row))

    def test_strict_rebuild_filters_exports_without_deleting_records(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            image_path = storage.image_path("example", "complete-primary", ".jpg")
            image_path.write_bytes(b"image")
            storage.write_json(
                storage.record_path("example", "complete-primary"),
                {
                    "record_id": "example:complete:primary",
                    "description": "Sourced description",
                    "description_status": "available",
                    "image": {
                        "status": "downloaded",
                        "local_path": storage.relative_path(image_path),
                    },
                },
            )
            storage.write_json(
                storage.record_path("example", "incomplete-primary"),
                {
                    "record_id": "example:incomplete:primary",
                    "description": None,
                    "description_status": "no_source",
                    "image": {"status": "no_image", "local_path": None},
                },
            )

            self.assertEqual(
                storage.rebuild_metadata(require_image=True, require_description=True),
                1,
            )
            exported = [
                json.loads(line)
                for line in storage.metadata_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [record["record_id"] for record in exported],
                ["example:complete:primary"],
            )
            self.assertTrue(storage.record_path("example", "incomplete-primary").exists())
            self.assertEqual(storage.rebuild_metadata(), 2)

    def test_content_filters_apply_to_existing_records(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            records = [
                ("inside", "Named Artist", 1790, 1810),
                ("outside", "Other Artist", 1700, 1750),
                ("anonymous", None, 1900, 1900),
                ("undated", "Known Artist", None, None),
            ]
            for record_id, creator_name, start_year, end_year in records:
                storage.write_json(
                    storage.record_path("example", record_id),
                    {
                        "record_id": f"example:{record_id}:primary",
                        "creators": ([{"name": creator_name}] if creator_name else None),
                        "creation_date": {
                            "start_year": start_year,
                            "end_year": end_year,
                        },
                    },
                )

            self.assertEqual(
                storage.rebuild_metadata(
                    require_creator=True,
                    year_from=1800,
                    year_to=2000,
                ),
                1,
            )
            exported = [
                json.loads(line)
                for line in storage.metadata_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([record["record_id"] for record in exported], ["example:inside:primary"])

    def test_resume_check_requires_downloaded_image_file(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            record_path = storage.record_path("example", "one")
            storage.write_json(
                record_path,
                {
                    "schema_version": "2.2",
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
                    "schema_version": "2.2",
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

    def test_old_schema_record_is_reprocessed(self):
        with self.temporary_directory() as temporary_directory:
            storage = DatasetStorage(Path(temporary_directory))
            storage.prepare("example")
            record_path = storage.record_path("example", "one")
            storage.write_json(
                record_path,
                {
                    "schema_version": "2.0",
                    "image": {"status": "no_image", "local_path": None},
                },
            )

            self.assertFalse(storage.record_is_complete(record_path, skip_images=False))


if __name__ == "__main__":
    unittest.main()
