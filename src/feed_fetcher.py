"""RSS Feed 获取与解析模块 —— ETag 缓存、短时缓存、指纹去重、Feed 级别并发锁"""
import asyncio
import calendar
import hashlib
import re
import time
import logging
from collections import OrderedDict
from urllib.parse import urlparse

import aiohttp
import feedparser

from .data_handler import DataHandler
from .rss import RSSItem


class FeedFetcher:
    """负责 RSS Feed 的网络请求、解析、ETag/内容缓存和并发控制"""

    def __init__(
        self,
        http_session: aiohttp.ClientSession,
        data_handler: DataHandler,
        title_max_length: int = 30,
        description_max_length: int = 500,
        max_items_per_poll: int = 3,
    ):
        self._http_session = http_session
        self._data_handler = data_handler
        self._title_max_length = title_max_length
        self._description_max_length = description_max_length
        self._max_items_per_poll = max_items_per_poll
        self._logger = logging.getLogger("astrbot")

        # ETag 缓存（LRU）
        self._etags: OrderedDict[str, str] = OrderedDict()
        self._max_etags = 500

        # 短时内容缓存：同 Feed 多订阅者在 TTL 内复用同一次请求结果
        self._cache: dict[str, tuple[bytes | None, float]] = {}
        self._cache_ttl = 60

        # 每 Feed 异步锁
        self._locks: dict[str, asyncio.Lock] = {}

        # 每域名并发限速（同域名最多 3 个并行请求）
        self._host_sem: dict[str, asyncio.Semaphore] = {}
        self._host_max = 3

    # ── Lock ──────────────────────────────────────────────

    def get_lock(self, url: str) -> asyncio.Lock:
        """获取指定 Feed 的异步锁，不存在则创建"""
        if url not in self._locks:
            self._locks[url] = asyncio.Lock()
        return self._locks[url]

    def _host_semaphore(self, url: str) -> asyncio.Semaphore:
        """获取域名级别的信号量，控制同站点并发数"""
        host = urlparse(url).netloc
        if host not in self._host_sem:
            self._host_sem[host] = asyncio.Semaphore(self._host_max)
        return self._host_sem[host]

    # ── Fetch ─────────────────────────────────────────────

    async def fetch(self, url: str) -> bytes | None:
        """获取 RSS XML，支持 ETag/304 + 短时缓存"""
        # 清理过期缓存
        now = time.time()
        stale = [u for u, (_, t) in self._cache.items() if now - t >= self._cache_ttl]
        for u in stale:
            del self._cache[u]

        # 命中缓存则直接返回
        if url in self._cache:
            cached, cached_time = self._cache[url]
            self._logger.debug(f"rss: {url} 命中缓存 ({(now - cached_time):.0f}s 前)")
            return cached

        try:
            headers = {}
            if url in self._etags:
                headers["If-None-Match"] = self._etags[url]
                self._etags.move_to_end(url)

            async with self._host_semaphore(url):
                async with self._http_session.get(url, headers=headers) as resp:
                    if resp.status == 304:
                        self._logger.debug(f"rss: {url} 内容未更新 (304)")
                        self._cache[url] = (None, time.time())
                        return None
                    if resp.status != 200:
                        self._logger.error(f"❌ rss: 无法正常打开站点 {url}, 状态码: {resp.status}")
                        return None

                    etag = resp.headers.get("ETag")
                    if etag:
                        self._etags[url] = etag
                        self._etags.move_to_end(url)
                        while len(self._etags) > self._max_etags:
                            old_url, _ = self._etags.popitem(last=False)
                            self._logger.debug(f"ETag 缓存淘汰: {old_url}")

                    content = await resp.read()
                    self._cache[url] = (content, time.time())
                    return content
        except asyncio.TimeoutError:
            self._logger.error(f"⏱️ rss: {url} 请求超时")
            return None
        except aiohttp.ClientError as e:
            self._logger.error(f"🌐 rss: {url} 网络错误: {str(e)}")
            return None
        except Exception as e:
            self._logger.error(f"💥 rss: {url} 未知错误: {str(e)}")
            return None

    # ── Poll / Parse ──────────────────────────────────────

    async def poll(
        self,
        url: str,
        num: int = -1,
        after_timestamp: int = 0,
        after_link: str = "",
        raw_text: bytes = None,
    ) -> list[RSSItem]:
        """从站点拉取 RSS 条目。raw_text 用于复用已获取的 XML。"""
        if raw_text is None:
            text = await self.fetch(url)
        else:
            text = raw_text
        if text is None:
            self._logger.debug(f"rss: {url} 无新内容或请求失败")
            return []

        feed = feedparser.parse(text)
        if feed.bozo:
            self._logger.warning(
                f"⚠️ rss: {url} 解析异常（已尽力恢复）: {feed.bozo_exception}"
            )

        cnt = 0
        rss_items = []

        chan_title = (
            self._data_handler.data[url]["info"]["title"]
            if url in self._data_handler.data
            else feed.feed.get("title", "未知频道")
        )

        for entry in feed.entries:
            try:
                title = entry.get("title", "无标题")
                if len(title) > self._title_max_length:
                    title = title[: self._title_max_length] + "..."

                link = entry.get("link", "")
                if link and not re.match(r"^https?://", link):
                    if link.startswith("//"):
                        link = "https:" + link
                    else:
                        link = self._data_handler.get_root_url(url) + link

                description = (entry.get("summary") or entry.get("description") or "")
                description, pic_url_list = self._data_handler.parse_html_text_and_pics(description)

                if len(description) > self._description_max_length:
                    description = description[: self._description_max_length] + "..."

                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_date_timestamp = calendar.timegm(entry.published_parsed)
                    pub_date = entry.get("published", "")
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_date_timestamp = calendar.timegm(entry.updated_parsed)
                    pub_date = entry.get("updated", "")
                else:
                    pub_date_timestamp = 0
                    pub_date = ""

                if pub_date_timestamp > 0:
                    if pub_date_timestamp > after_timestamp:
                        rss_items.append(
                            RSSItem(
                                chan_title, title, link, description,
                                pub_date, pub_date_timestamp, pic_url_list,
                            )
                        )
                        cnt += 1
                else:
                    if link != after_link:
                        if not after_link and len(rss_items) >= 1:
                            continue
                        rss_items.append(
                            RSSItem(
                                chan_title, title, link, description,
                                "", 0, pic_url_list,
                            )
                        )
                        cnt += 1

                if num != -1 and cnt >= num:
                    break

            except Exception as e:
                self._logger.error(f"❌ rss: 解析条目 [{url}] 失败: {str(e)}")
                continue

        rss_items.reverse()
        return rss_items

    # ── Utilities ─────────────────────────────────────────

    @staticmethod
    def fingerprint(title: str, link: str, description: str) -> str:
        """SHA-256 指纹，用于跨轮次条目去重"""
        material = f"{title}|{link}|{description[:300]}"
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    @staticmethod
    def cleanup_hashes(hashes: list, max_keep: int = 200) -> list:
        """限制哈希列表长度"""
        return hashes[-max_keep:] if len(hashes) > max_keep else hashes

    @staticmethod
    def normalize_url(url: str) -> str:
        """确保 URL 以 http:// 或 https:// 开头"""
        if not re.match(r"^https?://", url):
            if not url.startswith("/"):
                url = "/" + url
            url = "https://" + url
        return url
