"""消息构建模块 —— 将 RSSItem 组装为 AstrBot 消息链"""
import asyncio
from datetime import datetime, timezone, timedelta
import email.utils

import astrbot.api.message_components as Comp

from .pic_handler import RssImageHandler
from .rss import RSSItem


class MessageBuilder:
    """负责将 RSSItem 构建为 AstrBot 消息链（文本 + 图片）"""

    def __init__(
        self,
        pic_handler: RssImageHandler,
        max_pic_item: int = 3,
        is_read_pic: bool = False,
        is_hide_url: bool = False,
        t2i: bool = False,
    ):
        self._pic_handler = pic_handler
        self._max_pic_item = max_pic_item
        self._is_read_pic = is_read_pic
        self._is_hide_url = is_hide_url

    # ── Time ──────────────────────────────────────────────

    @staticmethod
    def format_time(item: RSSItem) -> str:
        """格式化发布时间为东八区友好的字符串"""
        tz_utc_8 = timezone(timedelta(hours=8))
        formatted_time = ""

        if item.pubDate:
            pub_date_str = str(item.pubDate).strip()
            try:
                dt = email.utils.parsedate_to_datetime(pub_date_str)
                formatted_time = dt.astimezone(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            if not formatted_time:
                try:
                    dt = datetime.strptime(pub_date_str, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(tz_utc_8)
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

        if not formatted_time and item.pubDate_timestamp > 0:
            try:
                dt_corrected = datetime.fromtimestamp(
                    item.pubDate_timestamp, tz=timezone.utc
                ).astimezone(tz_utc_8)
                formatted_time = dt_corrected.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        return formatted_time

    # ── Text ──────────────────────────────────────────────

    def build_text(self, item: RSSItem) -> str:
        """构建纯文本消息体"""
        text_parts = []

        text_parts.append(f"\U0001f4f0 【{item.chan_title}】")
        text_parts.append("━" * 40)

        formatted_time = self.format_time(item)
        if formatted_time:
            text_parts.append(f"\U0001f552 {formatted_time}")
        elif item.pubDate:
            text_parts.append(f"\U0001f552 {item.pubDate}")

        if not self._is_hide_url and item.link:
            text_parts.append(f"\U0001f517 {item.link}")

        text_parts.append("━" * 40)

        title = item.title.strip() if item.title else ""
        desc = item.description.strip() if item.description else ""

        if title and title != "无标题" and not desc.startswith(title[:10]):
            text_parts.append(f"\U0001f4cc {title}")
        if desc:
            desc = desc.replace("#", "\n#")
            text_parts.append(f"\U0001f4ac {desc}")

        return "\n".join(text_parts)

    # ── Images ────────────────────────────────────────────

    async def fetch_images(self, item: RSSItem) -> list:
        """并行下载图片，返回 Image / Plain 组件列表"""
        comps = []
        if self._is_read_pic and item.pic_urls:
            limit = (
                len(item.pic_urls)
                if self._max_pic_item == -1
                else self._max_pic_item
            )
            pic_urls = item.pic_urls[:limit]
            results = await asyncio.gather(
                *(
                    self._pic_handler.modify_corner_pixel_to_base64(url)
                    for url in pic_urls
                ),
                return_exceptions=True,
            )
            for base64str in results:
                if base64str is None or isinstance(base64str, Exception):
                    comps.append(Comp.Plain("\n[图片加载失败]"))
                else:
                    comps.append(Comp.Plain("\n"))
                    comps.append(Comp.Image.fromBase64(base64str))
        return comps

    # ── Chain ─────────────────────────────────────────────

    async def build_chain(self, item: RSSItem) -> list:
        """组装完整消息链（文本 + 图片）"""
        comps = [Comp.Plain(self.build_text(item))]
        image_comps = await self.fetch_images(item)
        comps.extend(image_comps)
        return comps
