"""默认排版 —— 频道名 / 时间 / 链接 / 标题 / 正文 / 图片"""
from .base import BaseRenderer


class DefaultRenderer(BaseRenderer):
    name = "default"
    max_desc = 500
    template = (
        "📰 【{chan_title}】\n"
        + "━" * 40 + "\n"
        "🕒 {time}\n"
        "🔗 {link}\n"
        + "━" * 40 + "\n"
        "📌 {title}\n"
        "💬 {description}"
    )
