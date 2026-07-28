"""PushManager tests"""
import asyncio
import tempfile
import pytest
from unittest.mock import MagicMock, AsyncMock

from src.push_mgr import PushManager
from src.data_handler import DataHandler


class MockFetcher:
    def __init__(self):
        self._locks = {}
    def get_lock(self, url):
        if url not in self._locks:
            self._locks[url] = asyncio.Lock()
        return self._locks[url]
    async def poll(self, url, **kw):
        from src.rss import RSSItem
        return [RSSItem("Chan", "Title", "https://x.com/1", "Body", "2026-07-28", 1722000000, [])]
    def fingerprint(self, *a):
        return "abc123"
    def cleanup_hashes(self, h, max_keep=200):
        return h[-max_keep:] if len(h) > max_keep else h


class MockBuilder:
    async def build_chain(self, item):
        return ["text"]
    def format_time(self, item):
        return "2026-07-28 12:00:00"
    async def fetch_images(self, item):
        return []


class MockRenderer:
    name = "test"
    def __init__(self, builder):
        pass
    async def render_item(self, item):
        return ["rendered"]
    async def send(self, context, user, items, t2i):
        return max((i.pubDate_timestamp for i in items), default=0)


@pytest.fixture
def dh():
    return DataHandler(tempfile.mktemp(suffix=".json"))


@pytest.fixture
def pm(dh):
    return PushManager(
        context=MagicMock(),
        data_handler=dh,
        fetcher=MockFetcher(),
        builder=MockBuilder(),
        renderers={"test": MockRenderer(MockBuilder())},
        max_consecutive_failures=3,
    )


class TestPushManager:
    def test_is_unrecoverable(self, pm):
        assert pm._is_unrecoverable(Exception("session not found"))
        assert pm._is_unrecoverable(Exception("user banned"))
        assert not pm._is_unrecoverable(Exception("timeout"))
        assert not pm._is_unrecoverable(Exception("unknown error"))

    def test_record_history(self, pm):
        from src.rss import RSSItem
        item = RSSItem("Chan", "Title", "https://x.com/1", "Body", "", 0, [])
        pm._record_history("https://feed", "user_a", item)
        history = pm._data_handler.data["_push_history"]
        assert len(history) == 1
        assert history[0]["title"] == "Title"
        assert history[0]["url"] == "https://feed"

    def test_record_history_truncates(self, pm):
        from src.rss import RSSItem
        # pre-fill 199 items
        pm._data_handler.data["_push_history"] = [{"time": 0, "chan_title": "", "title": "", "url": "", "user": ""}] * 199
        for _ in range(5):
            pm._record_history("url", "user", RSSItem("C", "T", "L", "D", "", 0, []))
        assert len(pm._data_handler.data["_push_history"]) == 200

    def test_queue_failed(self, pm):
        from src.rss import RSSItem
        item = RSSItem("C", "T", "L", "D", "2026", 100, ["pic"])
        pm._queue_failed("user_a", "url_x", item)
        failed = pm._data_handler.data["_failed_pushes"]
        assert len(failed) == 1
        assert failed[0]["item"]["title"] == "T"
        assert failed[0]["item"]["pic_urls"] == ["pic"]

    def test_on_failure_increments(self, pm):
        pm._data_handler.data = {"http://feed": {"subscribers": {"u": {"pushed_hashes": []}}}}
        pm._on_failure(pm._data_handler.data, "http://feed", "u", "test")
        assert pm._data_handler.data["http://feed"]["subscribers"]["u"]["consecutive_failures"] == 1

    def test_on_failure_auto_pauses(self, pm):
        pm._data_handler.data = {"http://feed": {"subscribers": {"u": {"pushed_hashes": [], "consecutive_failures": 2}}}}
        pm._on_failure(pm._data_handler.data, "http://feed", "u", "test")
        sub = pm._data_handler.data["http://feed"]["subscribers"]["u"]
        assert sub["consecutive_failures"] == 3
        assert sub["paused"] is True

    @pytest.mark.asyncio
    async def test_execute_skips_paused(self, pm):
        pm._data_handler.data = {
            "http://feed": {
                "subscribers": {"u": {"paused": True, "pushed_hashes": [], "last_update": 0, "latest_link": ""}},
                "info": {"title": "Test"},
            }
        }
        # Should return early without error
        await pm.execute("http://feed", "u")

    @pytest.mark.asyncio
    async def test_execute_with_new_items(self, pm):
        pm._data_handler.data = {
            "http://feed": {
                "subscribers": {"u": {"pushed_hashes": [], "last_update": 0, "latest_link": ""}},
                "info": {"title": "Test"},
            }
        }
        await pm.execute("http://feed", "u")
        sub = pm._data_handler.data["http://feed"]["subscribers"]["u"]
        assert sub["last_update"] > 0
        assert sub["pushed_hashes"] == ["abc123"]
        assert sub["consecutive_failures"] == 0

    def test_pick_renderer_auto_selects(self, pm):
        pm._data_handler.data = {
            "http://feed": {
                "subscribers": {"aiocqhttp:123:user": {"pushed_hashes": []}},
                "info": {"title": "T"},
            }
        }
        r = pm._pick_renderer("aiocqhttp:123:user", "http://feed")
        assert r.name == "test"
