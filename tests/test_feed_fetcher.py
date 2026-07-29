"""FeedFetcher tests"""
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

import tempfile
import aiohttp

from src.feed_fetcher import FeedFetcher
from src.data_handler import DataHandler


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Test Blog</title>
<link>https://example.com</link>
<description>A test blog</description>
<item>
  <title>First Post</title>
  <link>https://example.com/post1</link>
  <description>Hello world</description>
  <pubDate>Mon, 28 Jul 2026 12:00:00 GMT</pubDate>
  <guid>https://example.com/post1</guid>
</item>
<item>
  <title>Second Post</title>
  <link>https://example.com/post2</link>
  <description>Another post</description>
  <pubDate>Mon, 28 Jul 2026 14:00:00 GMT</pubDate>
  <guid>https://example.com/post2</guid>
</item>
</channel></rss>"""


@pytest.fixture
def dh():
    import tempfile
    d = DataHandler(tempfile.mktemp(suffix=".json"))
    d.data = {}
    return d


@pytest.fixture
def mock_session():
    session = AsyncMock(spec=aiohttp.ClientSession)
    session.closed = False
    return session


@pytest.fixture
def fetcher(dh, mock_session):
    return FeedFetcher(mock_session, dh)


class TestFeedFetcher:
    def test_normalize_url(self, fetcher):
        assert "https://" in fetcher.normalize_url("example.com/feed")
        assert fetcher.normalize_url("http://example.com") == "http://example.com"

    def test_fingerprints_deterministic(self, fetcher):
        f1 = fetcher.fingerprints("G1", "L", "T", "D")
        f2 = fetcher.fingerprints("G1", "L", "T", "D")
        assert f1 == f2
        assert len(f1) == 3  # guid, sid, hash

    def test_fingerprints_different_content(self, fetcher):
        f1 = fetcher.fingerprints("G1", "L", "T", "D")
        f2 = fetcher.fingerprints("G2", "L", "T", "D")
        assert f1 != f2

    def test_cleanup_hashes(self, fetcher):
        hashes = [str(i) for i in range(300)]
        result = fetcher.cleanup_hashes(hashes, max_keep=200)
        assert len(result) == 200
        assert result[-1] == "299"

    def test_get_lock_creates_and_reuses(self, fetcher):
        lock1 = fetcher.get_lock("url_a")
        lock2 = fetcher.get_lock("url_a")
        lock3 = fetcher.get_lock("url_b")
        assert lock1 is lock2
        assert lock1 is not lock3

    @pytest.mark.asyncio
    async def test_poll_parses_rss(self, fetcher, dh):
        dh.data = {"https://example.com/feed": {"info": {"title": "My Feed"}}}
        items = await fetcher.poll("https://example.com/feed", raw_text=SAMPLE_RSS)
        assert len(items) == 2
        # Items are reversed (oldest first)
        assert len(items) == 2
        titles = [it.title for it in items]
        assert "First Post" in titles
        assert "Second Post" in titles
        assert items[0].chan_title == "My Feed"

    @pytest.mark.asyncio
    async def test_poll_respects_num(self, fetcher, dh):
        items = await fetcher.poll("https://example.com/feed", num=1, raw_text=SAMPLE_RSS)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_poll_respects_after_timestamp(self, fetcher, dh):
        # Only second post should be included (> 12:00 UTC)
        # "Mon, 28 Jul 2026 14:00:00 GMT" = 1785247200
        # filter after first post's timestamp → only second post
        items = await fetcher.poll(
            "https://example.com/feed",
            raw_text=SAMPLE_RSS,
            after_timestamp=1785240000  # between the two posts
        )
        assert len(items) == 1
        assert "Second Post" in items[0].title

    @pytest.mark.asyncio
    async def test_poll_empty_on_none_text(self, fetcher):
        items = await fetcher.poll("https://example.com/feed", raw_text=None)
        assert items == []

    def test_host_semaphore_creates_per_host(self, fetcher):
        sem1 = fetcher._host_semaphore("https://rsshub.app/path")
        sem2 = fetcher._host_semaphore("https://rsshub.app/other")
        sem3 = fetcher._host_semaphore("https://other.com/feed")
        assert sem1 is sem2
        assert sem1 is not sem3
