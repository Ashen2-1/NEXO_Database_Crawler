import unittest

from nexo_crawler.enrichers.wikidata import fetch_wikidata_description


class FakeJsonClient:
    def __init__(self, response):
        self.response = response
        self.url = None

    def get_json(self, url):
        self.url = url
        return self.response


class WikidataEnricherTests(unittest.TestCase):
    def test_fetches_english_label_and_description_for_known_entity(self):
        client = FakeJsonClient(
            {
                "entities": {
                    "Q42": {
                        "labels": {"en": {"language": "en", "value": "Example artwork"}},
                        "descriptions": {
                            "en": {"language": "en", "value": "portrait painting"}
                        },
                    }
                }
            }
        )

        result = fetch_wikidata_description("https://www.wikidata.org/wiki/Q42", client)

        self.assertEqual(result["description_status"], "available")
        self.assertEqual(result["description"], "portrait painting")
        self.assertEqual(result["wikidata_label"], "Example artwork")
        self.assertEqual(result["wikidata_entity_id"], "Q42")
        self.assertIn("Special:EntityData/Q42.json", client.url)

    def test_known_entity_without_english_description_is_not_available(self):
        result = fetch_wikidata_description(
            "https://www.wikidata.org/wiki/Q42",
            FakeJsonClient({"entities": {"Q42": {"labels": {}, "descriptions": {}}}}),
        )

        self.assertEqual(result["description_status"], "not_available")
        self.assertIsNone(result["description"])

    def test_missing_entity_url_does_not_make_a_request(self):
        client = FakeJsonClient({})

        result = fetch_wikidata_description(None, client)

        self.assertEqual(result, {"description_status": "no_source"})
        self.assertIsNone(client.url)


if __name__ == "__main__":
    unittest.main()
