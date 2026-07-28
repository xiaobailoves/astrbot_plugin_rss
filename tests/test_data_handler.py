"""DataHandler tests"""
import json
import os
import tempfile
import pytest
import asyncio

from src.data_handler import DataHandler


class TestDataHandler:
    def test_load_creates_default(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            os.unlink(path)
            dh = DataHandler(path)
            assert dh.data == {"rsshub_endpoints": []}
        finally:
            os.unlink(path)

    def test_load_existing(self):
        data = {"rsshub_endpoints": ["https://rsshub.app"], "test_url": {"subscribers": {}, "info": {"title": "T"}}}
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as f:
            json.dump(data, f)
            path = f.name
        try:
            dh = DataHandler(path)
            assert dh.data["rsshub_endpoints"] == ["https://rsshub.app"]
        finally:
            os.unlink(path)

    def test_load_corrupted_backup(self):
        path = tempfile.mktemp(suffix=".json")
        with open(path, "w") as f:
            f.write("not valid json {{{")
        dh = DataHandler(path)
        assert dh.data == {"rsshub_endpoints": []}
        # backup should exist
        assert os.path.exists(path + ".corrupted_backup")
        os.unlink(path)
        os.unlink(path + ".corrupted_backup")

    def test_get_subs_channel_url(self):
        dh = DataHandler(tempfile.mktemp(suffix=".json"))
        dh.data = {
            "rsshub_endpoints": [],
            "http://example.com/feed": {
                "subscribers": {"user_a": {"cron_expr": "* * * * *"}},
                "info": {"title": "Test"},
            },
            "http://other.com/rss": {
                "subscribers": {"user_b": {"cron_expr": "0 * * * *"}},
                "info": {"title": "Other"},
            },
        }
        assert dh.get_subs_channel_url("user_a") == ["http://example.com/feed"]
        assert dh.get_subs_channel_url("user_b") == ["http://other.com/rss"]
        assert dh.get_subs_channel_url("nobody") == []

    def test_get_subs_skips_internal_keys(self):
        dh = DataHandler(tempfile.mktemp(suffix=".json"))
        dh.data = {
            "rsshub_endpoints": [],
            "settings": {"config": {}},
            "_push_history": [],
            "_failed_pushes": [],
            "http://real.com": {"subscribers": {"u": {"cron_expr": "0 * * * *"}}, "info": {"title": "R"}},
        }
        # internal keys should NOT appear in channel list
        subs = dh.get_subs_channel_url("u")
        assert len(subs) == 1
        assert subs[0] == "http://real.com"

    def test_parse_html_extracts_text_and_images(self):
        dh = DataHandler(tempfile.mktemp(suffix=".json"))
        html = '<p>Hello <b>World</b></p><img src="http://img1.jpg"/><br><img src="http://img2.png"/>'
        text, pics = dh.parse_html_text_and_pics(html)
        assert "Hello" in text
        assert "World" in text
        assert len(pics) == 2
        assert "http://img1.jpg" in pics

    def test_get_root_url(self):
        dh = DataHandler(tempfile.mktemp(suffix=".json"))
        assert dh.get_root_url("https://example.com/path/to/feed") == "https://example.com"
        assert dh.get_root_url("http://sub.domain.com:8080/rss") == "http://sub.domain.com:8080"

    def test_parse_channel_text_info(self):
        dh = DataHandler(tempfile.mktemp(suffix=".json"))
        rss = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
        <title>My Blog</title>
        <description>A blog about stuff</description>
        </channel></rss>"""
        title, desc = dh.parse_channel_text_info(rss.encode())
        assert title == "My Blog"
        assert "blog" in desc.lower()

    def test_save_and_load_roundtrip(self):
        path = tempfile.mktemp(suffix=".json")
        dh = DataHandler(path)
        dh.data["test_key"] = "hello"
        asyncio.run(dh.save_data())
        dh2 = DataHandler(path)
        assert dh2.data["test_key"] == "hello"
        os.unlink(path)
