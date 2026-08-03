import argparse
import unittest

from nexo_crawler.models import ImageInfo
from nexo_crawler.sources.base import NormalizationContext
from nexo_crawler.sources.metmuseum import MetMuseumAdapter


class FakeJsonClient:
    def __init__(self, response):
        self.response = response
        self.url = None

    def get_json(self, url):
        self.url = url
        return self.response


class MetMuseumAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MetMuseumAdapter()

    def arguments(self, **overrides):
        values = {
            "object_id": [],
            "ids_file": None,
            "query": None,
            "department_id": None,
            "include_results_without_images": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_search_adds_image_filter_and_department(self):
        client = FakeJsonClient({"objectIDs": [4, 7]})
        result = self.adapter.discover(
            self.arguments(query="blue vase", department_id=5),
            client,
        )
        self.assertEqual(result, ["4", "7"])
        self.assertIn("hasImages=true", client.url)
        self.assertIn("departmentId=5", client.url)

    def test_search_omits_image_filter_when_requested(self):
        client = FakeJsonClient({"objectIDs": [4]})
        self.adapter.discover(
            self.arguments(query="blue vase", include_results_without_images=True),
            client,
        )
        self.assertNotIn("hasImages", client.url)

    def test_met_fields_map_to_canonical_schema_without_description(self):
        raw = {
            "objectID": 42,
            "objectURL": "https://www.metmuseum.org/art/collection/search/42",
            "isPublicDomain": True,
            "primaryImage": "https://images.example.test/42.jpg",
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
        candidate = self.adapter.image_candidates("42", raw)[0]
        context = NormalizationContext(
            identity=self.adapter.identity("42", candidate),
            retrieved_at="2026-01-01T00:00:00Z",
            raw_path="raw/metmuseum/MET-42.json",
            image=ImageInfo(
                role="primary",
                status="downloaded",
                source_url=candidate.source_url,
                local_path="images/metmuseum/MET-42-primary.jpg",
                sha256="abc",
                bytes=123,
                content_type="image/jpeg",
            ),
        )
        record = self.adapter.normalize("42", raw, context).to_dict()

        self.assertEqual(record["record_id"], "metmuseum:42:primary")
        self.assertEqual(record["title"], "Source title")
        self.assertIsNone(record["description"])
        self.assertEqual(record["creators"][0]["name"], "Source Artist")
        self.assertEqual(record["country"], "United States")
        self.assertEqual(record["tags"], ["Trees"])
        self.assertIsNone(record["brand"])
        self.assertEqual(record["annotation"]["method"], "none")

    def test_non_public_domain_image_is_blocked_by_adapter(self):
        candidate = self.adapter.image_candidates(
            "42",
            {"primaryImage": "https://example.test/42.jpg", "isPublicDomain": False},
        )[0]
        self.assertFalse(candidate.download_allowed)
        self.assertEqual(candidate.blocked_status, "not_public_domain")


if __name__ == "__main__":
    unittest.main()
