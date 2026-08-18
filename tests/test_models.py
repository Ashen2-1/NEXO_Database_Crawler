import unittest

from nexo_crawler.models import CanonicalRecord, ImageInfo, SourceInfo


class CanonicalRecordTests(unittest.TestCase):
    def source(self):
        return SourceInfo(
            key="example",
            name="Example Source",
            object_id="123",
            api_url=None,
            page_url="https://example.test/123",
            retrieved_at="2026-01-01T00:00:00Z",
            raw_path="raw/example/123.json",
        )

    def test_missing_common_fields_are_explicit_nulls(self):
        record = CanonicalRecord(
            record_id="example:123:primary",
            source=self.source(),
            image=ImageInfo(role="primary", status="no_image", source_url=None),
        ).to_dict()

        self.assertEqual(record["schema_version"], "2.2")
        self.assertIsNone(record["title"])
        self.assertIsNone(record["description"])
        self.assertEqual(record["description_status"], "not_requested")
        self.assertIsNone(record["brand"])
        self.assertIsNone(record["model"])
        self.assertIsNone(record["classification"])
        self.assertIsNone(record["city"])
        self.assertIsNone(record["region"])
        self.assertIsNone(record["repository"])
        self.assertIsNone(record["creators"])
        self.assertEqual(record["source_metadata"], {})

    def test_downloaded_image_requires_provenance_values(self):
        record = CanonicalRecord(
            record_id="example:123:primary",
            source=self.source(),
            image=ImageInfo(
                role="primary",
                status="downloaded",
                source_url="https://example.test/image.jpg",
                local_path="images/example/image.jpg",
            ),
        )
        with self.assertRaisesRegex(ValueError, "path, hash, and byte count"):
            record.to_dict()


if __name__ == "__main__":
    unittest.main()
