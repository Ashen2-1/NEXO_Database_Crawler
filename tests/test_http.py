import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from nexo_crawler.http import HttpAccessBlockedError, HttpClient


class HttpClientTests(unittest.TestCase):
    def client(self, retries=3):
        return HttpClient(
            timeout=30.0,
            retries=retries,
            request_delay=0.0,
            user_agent="test-agent",
        )

    @patch("nexo_crawler.http.time.sleep")
    @patch("nexo_crawler.http.urllib.request.urlopen")
    def test_403_retries_with_stronger_backoff_then_halts(self, urlopen, sleep):
        url = "https://api.example.test/object/1"
        urlopen.side_effect = [
            urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            for _ in range(4)
        ]

        with self.assertRaises(HttpAccessBlockedError):
            self.client(retries=3).get(url)

        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10, 20])

    @patch("nexo_crawler.http.time.sleep")
    @patch("nexo_crawler.http.urllib.request.urlopen")
    def test_403_can_recover_on_retry(self, urlopen, sleep):
        url = "https://api.example.test/object/1"
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"{}"
        response.headers.get_content_type.return_value = "application/json"
        response.geturl.return_value = url
        urlopen.side_effect = [
            urllib.error.HTTPError(url, 403, "Forbidden", {}, None),
            response,
        ]

        client = self.client(retries=3)
        result = client.get(url)

        self.assertEqual(result.data, b"{}")
        self.assertEqual(client._effective_request_delay, 1.0)
        sleep.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
