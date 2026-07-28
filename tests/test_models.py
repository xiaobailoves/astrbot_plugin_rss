"""Models / Renderer tests"""
import pytest
import asyncio


# ── Mock builder ──

class MockBuilder:
    """Simulates MessageBuilder for renderer testing"""
    def format_time(self, item):
        return "2026-07-28 12:00:00"

    async def fetch_images(self, item):
        return []

    async def build_chain(self, item):
        return [f"default: {item.title}"]


# ── Mock RSSItem ──

class MockItem:
    def __init__(self, chan_title="Test Channel", title="Test Title",
                 link="https://example.com", description="Test body",
                 pubDate="", pubDate_timestamp=0, pic_urls=None):
        self.chan_title = chan_title
        self.title = title
        self.link = link
        self.description = description
        self.pubDate = pubDate
        self.pubDate_timestamp = pubDate_timestamp
        self.pic_urls = pic_urls or []


# ── Tests ──

class TestDefaultRenderer:
    def test_template_renders_basic(self):
        from src.models.base import BaseRenderer
        class R(BaseRenderer):
            name = "test"
            template = "📰 {chan_title}\n{title}"
        r = R(MockBuilder())
        item = MockItem(chan_title="My Feed", title="Hello")
        result = asyncio.run(r.render_item(item))
        assert "My Feed" in str(result[0])
        assert "Hello" in result[0]

    def test_max_desc_truncates(self):
        from src.models.base import BaseRenderer
        class R(BaseRenderer):
            name = "test"
            template = "{description}"
            max_desc = 10
        r = R(MockBuilder())
        item = MockItem(description="A" * 50)
        result = asyncio.run(r.render_item(item))
        assert len(result[0]) <= 10

    def test_show_images_false(self):
        from src.models.base import BaseRenderer
        class R(BaseRenderer):
            name = "test"
            template = "{title}"
            show_images = False
        r = R(MockBuilder())
        item = MockItem(pic_urls=["http://img.jpg"])
        result = asyncio.run(r.render_item(item))
        assert len(result) == 1  # no image component

    def test_format_context_override(self):
        from src.models.base import BaseRenderer
        class R(BaseRenderer):
            name = "test"
            template = "{badge} {title}"
            def _format_context(self, item):
                ctx = super()._format_context(item)
                ctx["badge"] = "🔥"
                return ctx
        r = R(MockBuilder())
        result = asyncio.run(r.render_item(MockItem(title="Hot News")))
        assert result[0].startswith("🔥")

    def test_default_falls_back_to_builder(self):
        from src.models.base import BaseRenderer
        class R(BaseRenderer):
            name = "test"
            # no template → use builder
        r = R(MockBuilder())
        result = asyncio.run(r.render_item(MockItem(title="X")))
        assert "default: X" in str(result)


class TestTwitterRenderer:
    def test_rt_prefix(self):
        from src.models.twitter import TwitterRenderer
        r = TwitterRenderer(MockBuilder())
        item = MockItem(title="RT someone: hello", chan_title="Twitter @user")
        result = asyncio.run(r.render_item(item))
        text = result[0]
        assert "RT" not in text  # RT prefix replaced with emoji
        assert "Twitter @user" in text
        assert "Twitter @user" in text

    def test_non_rt_no_prefix(self):
        from src.models.twitter import TwitterRenderer
        r = TwitterRenderer(MockBuilder())
        item = MockItem(title="Just a tweet", chan_title="Twitter @user")
        result = asyncio.run(r.render_item(item))
        text = result[0]
        assert "🔁" not in text
