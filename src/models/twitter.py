"""Twitter 风格 —— 去标题（和正文重复），转发加 🔁 标记"""
from .base import BaseRenderer


class TwitterRenderer(BaseRenderer):
    name = "twitter"
    max_desc = 300
    template = (
        "{prefix}🐦 {chan_title}\n"
        "🕒 {time}\n"
        "🔗 {link}\n"
        + "─" * 30 + "\n"
        "💬 {description}"
    )

    def _format_context(self, item):
        ctx = super()._format_context(item)
        is_rt = item.title.strip().startswith("RT ") if item.title else False
        ctx["prefix"] = "🔁 " if is_rt else ""
        return ctx
