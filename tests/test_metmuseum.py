import argparse
import unittest

from nexo_crawler.models import ImageInfo
from nexo_crawler.sources.base import NormalizationContext
from nexo_crawler.sources.metmuseum import MetMuseumAdapter


class FakeJsonClient:
    def __init__(self, response):
        self.response = response
        self.url = None
        self.urls = []

    def get_json(self, url):
        self.url = url
        self.urls.append(url)
        return self.response


class MetMuseumAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = MetMuseumAdapter()

    def arguments(self, **overrides):
        values = {
            "object_id": [],
            "ids_file": None,
            "query": None,
            "target": [],
            "person_scope": "standard",
            "all_objects": False,
            "department_id": [],
            "updated_since": None,
            "include_results_without_images": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_search_adds_image_filter_and_department(self):
        client = FakeJsonClient({"objectIDs": [4, 7]})
        result = self.adapter.discover(
            self.arguments(query="blue vase", department_id=[5]),
            client,
        )
        self.assertEqual(result.source_ids, ["4", "7"])
        self.assertEqual(result.method, "query")
        self.assertIn("hasImages=true", client.url)
        self.assertIn("departmentId=5", client.url)

    def test_search_omits_image_filter_when_requested(self):
        client = FakeJsonClient({"objectIDs": [4]})
        self.adapter.discover(
            self.arguments(query="blue vase", include_results_without_images=True),
            client,
        )
        self.assertNotIn("hasImages", client.url)

    def test_target_discovery_uses_curated_image_search(self):
        client = FakeJsonClient({"total": 2, "objectIDs": [4, 7]})
        result = self.adapter.discover(self.arguments(target=["architecture"]), client)

        self.assertEqual(result.source_ids, ["4", "7"])
        self.assertEqual(result.method, "targets")
        self.assertNotIn("tags=true", client.url)
        self.assertIn("hasImages=true", client.url)
        self.assertEqual(result.parameters["targets"], ["architecture"])

    def test_target_match_uses_object_metadata_after_discovery(self):
        self.assertTrue(
            self.adapter.matches_targets(
                {"tags": [{"term": "Portraits"}]},
                ("person",),
            )
        )
        self.assertFalse(
            self.adapter.matches_targets(
                {"title": "Oak side chair", "objectName": "Chair"},
                ("person",),
            )
        )

    def test_broad_person_scope_adds_controlled_person_searches(self):
        client = FakeJsonClient({"total": 2, "objectIDs": [4, 7]})
        result = self.adapter.discover(
            self.arguments(target=["person"], person_scope="broad"),
            client,
        )

        self.assertEqual(result.source_ids, ["4", "7"])
        self.assertEqual(result.parameters["person_scope"], "broad")
        self.assertTrue(any("q=People" in url for url in client.urls))
        self.assertTrue(any("q=Human+Figures" in url for url in client.urls))
        self.assertTrue(any("q=Self-Portrait" in url for url in client.urls))
        self.assertTrue(
            all("isHighlight=true" in url for url in client.urls if "q=Men" in url)
        )
        self.assertTrue(
            all("isHighlight=true" in url for url in client.urls if "q=Women" in url)
        )

    def test_broad_person_scope_requires_person_target(self):
        with self.assertRaisesRegex(ValueError, "requires --target person"):
            self.adapter.discover(
                self.arguments(target=["architecture"], person_scope="broad"),
                FakeJsonClient({}),
            )

    def test_target_cannot_be_combined_with_query(self):
        with self.assertRaisesRegex(ValueError, "--target cannot be combined"):
            self.adapter.discover(
                self.arguments(target=["person"], query="portrait"),
                FakeJsonClient({}),
            )

    def test_all_discovers_inventory_with_department_and_update_filters(self):
        client = FakeJsonClient({"total": 2, "objectIDs": [8, 9]})
        result = self.adapter.discover(
            self.arguments(
                all_objects=True,
                department_id=[5, 11],
                updated_since="2026-01-01",
            ),
            client,
        )
        self.assertEqual(result.source_ids, ["8", "9"])
        self.assertEqual(result.total_reported, 2)
        self.assertEqual(result.method, "all")
        self.assertIn("departmentIds=5%7C11", client.url)
        self.assertIn("metadataDate=2026-01-01", client.url)

    def test_all_cannot_be_combined_with_query(self):
        with self.assertRaisesRegex(ValueError, "--all cannot be combined"):
            self.adapter.discover(
                self.arguments(all_objects=True, query="vase"),
                FakeJsonClient({}),
            )

    def test_updated_since_requires_all(self):
        with self.assertRaisesRegex(ValueError, "--updated-since requires --all"):
            self.adapter.discover(
                self.arguments(query="vase", updated_since="2026-01-01"),
                FakeJsonClient({}),
            )

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
            "artistAlphaSort": "Artist, Source",
            "artistNationality": "American",
            "country": "United States",
            "region": "North America",
            "city": "New York",
            "geographyType": "Made in",
            "repository": "Example Repository",
            "objectWikidata_URL": "https://www.wikidata.org/wiki/Q42",
            "GalleryNumber": "100",
            "isHighlight": False,
            "isTimelineWork": True,
            "tags": [{"term": "Trees"}, {"term": "Portraits"}],
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
        self.assertEqual(record["creators"][0]["sort_name"], "Artist, Source")
        self.assertEqual(record["classification"], "Photographs")
        self.assertEqual(record["country"], "United States")
        self.assertEqual(record["region"], "North America")
        self.assertEqual(record["city"], "New York")
        self.assertEqual(record["geography_type"], "Made in")
        self.assertEqual(record["repository"], "Example Repository")
        self.assertEqual(record["gallery_number"], "100")
        self.assertFalse(record["is_highlight"])
        self.assertTrue(record["is_timeline_work"])
        self.assertEqual(record["tags"], ["Trees", "Portraits"])
        self.assertIsNone(record["brand"])
        self.assertEqual(record["annotation"]["method"], "none")
        self.assertTrue(record["target_person"])
        self.assertFalse(record["target_architecture"])
        self.assertFalse(record["target_painting"])

    def test_non_public_domain_image_is_blocked_by_adapter(self):
        candidate = self.adapter.image_candidates(
            "42",
            {"primaryImage": "https://example.test/42.jpg", "isPublicDomain": False},
        )[0]
        self.assertFalse(candidate.download_allowed)
        self.assertEqual(candidate.blocked_status, "not_public_domain")


if __name__ == "__main__":
    unittest.main()
