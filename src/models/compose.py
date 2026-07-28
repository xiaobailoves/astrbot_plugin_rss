"""QQ 合并转发渲染器 —— 多条条目打包为一条转发消息"""
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain

from .base import BaseRenderer


class ComposeRenderer(BaseRenderer):
    """将多条 RSS 条目打包为 QQ 合并转发消息"""

    name = "compose"

    async def send(self, context, user: str, items: list, t2i: bool) -> int:
        nodes = []
        max_ts = 0
        for item in items:
            comps = await self.render_item(item)
            nodes.append(Comp.Node(uin=0, name="Astrbot", content=comps))
            max_ts = max(max_ts, item.pubDate_timestamp)

        msc = MessageChain(chain=nodes, use_t2i_=t2i)
        await context.send_message(user, msc)
        return max_ts
