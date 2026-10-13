import unittest
from unittest.mock import Mock, patch

import requests

from src.platforms import feed
from src.platforms.rss import RssFetcher
from src.platforms.rsshub import RsshubFetcher


def response(body=b"", *, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {}, url="https://example.com/feed.xml")
    result.iter_content.return_value = [body]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class RssTests(unittest.TestCase):
    def test_feed_parses_relative_links_and_limits_entries(self):
        body = ('<rss version="2.0"><channel><title>Test</title>' +
                ''.join(f'<item><title>Item {i}</title><link>/article/{i}</link></item>' for i in range(5)) +
                '</channel></rss>').encode()
        with patch.object(feed.requests, "get", return_value=response(body)) as get:
            articles = RssFetcher().fetch("https://example.com/feed.xml")
        self.assertEqual(len(articles), 3)
        self.assertEqual(articles[0]["link"], "https://example.com/article/0")
        self.assertTrue(get.call_args.kwargs["stream"])
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_atom_updated_time_is_preserved(self):
        body = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title><entry><title>Item</title><updated>2026-10-12T00:00:00Z</updated></entry></feed>'
        with patch.object(feed.requests, "get", return_value=response(body)):
            articles = feed.fetch_feed("https://example.com/feed")
        self.assertEqual(articles[0]["published"], "2026-10-12T00:00:00Z")

    def test_local_file_and_credential_urls_are_rejected_before_request(self):
        with patch.object(feed.requests, "get") as get:
            for url in ("file:///C:/secret", "C:/secret", "https://user:pass@example.com/rss", "https://example.com:bad/rss"):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    feed.fetch_feed(url)
        get.assert_not_called()

    def test_redirect_to_local_file_is_rejected(self):
        with patch.object(feed.requests, "get", return_value=response(status=302, headers={"Location": "file:///C:/secret"})) as get:
            with self.assertRaises(ValueError):
                feed.fetch_feed("https://example.com/feed")
        self.assertEqual(get.call_count, 1)

    def test_redirect_loop_is_bounded(self):
        with patch.object(feed.requests, "get", return_value=response(status=302, headers={"Location": "/feed"})) as get:
            with self.assertRaisesRegex(RuntimeError, "重定向"):
                feed.fetch_feed("https://example.com/feed")
        self.assertEqual(get.call_count, feed.MAX_REDIRECTS + 1)

    def test_decompressed_body_is_bounded_before_parsing(self):
        with patch.object(feed.requests, "get", return_value=response(b"12345")), patch.object(feed, "MAX_FEED_BYTES", 4), patch.object(feed.feedparser, "parse") as parse:
            with self.assertRaisesRegex(RuntimeError, "上限"):
                feed.fetch_feed("https://example.com/feed")
        parse.assert_not_called()

    def test_deadline_bounds_slow_stream(self):
        with patch.object(feed.requests, "get", return_value=response(b"rss")), patch.object(feed.time, "monotonic", side_effect=[0, 1, 31]):
            with self.assertRaisesRegex(RuntimeError, "时间上限"):
                feed.fetch_feed("https://example.com/feed")

    def test_transport_error_does_not_echo_private_feed_token(self):
        with patch.object(feed.requests, "get", side_effect=requests.Timeout("https://example.com/feed?token=private")):
            with self.assertRaises(RuntimeError) as raised:
                feed.fetch_feed("https://example.com/feed?token=private")
        self.assertNotIn("private", str(raised.exception))

    def test_http_errors_and_malformed_feeds_are_reported(self):
        for item in (response(status=503), response(b"not XML")):
            with patch.object(feed.requests, "get", return_value=item), self.assertRaises(RuntimeError):
                feed.fetch_feed("https://example.com/feed")

    def test_local_rsshub_is_supported_and_uid_cannot_inject_query(self):
        with patch("src.platforms.rsshub.fetch_feed", return_value=[]) as fetch:
            self.assertEqual(RsshubFetcher("/weibo/user/{uid}").fetch("123?limit=999"), [])
        fetch.assert_called_once_with("http://localhost:1200/weibo/user/123%3Flimit%3D999")
