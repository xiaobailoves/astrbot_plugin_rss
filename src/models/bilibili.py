"""示例：Bilibili 定制解析 — 从描述中提取播放/弹幕/收藏数据"""
import re
from .base import BaseRenderer


class BilibiliRenderer(BaseRenderer):
    """B 站动态：提取播放量、弹幕数等统计"""

    name = "bilibili"

    template = (
        "📺 {chan_title}\n"
        "🕒 {time}\n"
        "🔗 {link}\n"
        + "─" * 30 + "\n"
        "📌 {title}\n"
        "{stats}"
        "💬 {description}"
    )

    async def parse_item(self, item):
        desc = item.description or ""

        # 提取各项数据（RSSHub B 站路由常见格式）
        play = re.search(r'播放[：:]?\s*([\d.]+万?)', desc)
        danmu = re.search(r'弹幕[：:]?\s*([\d.]+万?)', desc)
        fav = re.search(r'收藏[：:]?\s*([\d.]+万?)', desc)

        parts = []
        if play: parts.append(f"▶️ {play.group(1)}播放")
        if danmu: parts.append(f"💬 {danmu.group(1)}弹幕")
        if fav: parts.append(f"⭐ {fav.group(1)}收藏")

        # 把提取结果存到 item 的自定义属性
        item._stats = " | ".join(parts) + "\n" if parts else ""

        # 从正文中移除已提取的统计行（避免重复显示）
        item.description = re.sub(
            r'^(UP主|播放|弹幕|收藏|投币|点赞|分享|阅读|评论)[：:].*\n?',
            '', desc, flags=re.MULTILINE
        ).strip()

        return item

    def _format_context(self, item):
        ctx = super()._format_context(item)
        ctx["stats"] = getattr(item, '_stats', '')
        return ctx
