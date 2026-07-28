"""渲染器基类。

两种自定义方式（由简到繁）：

1. 模板模式 — 只需设 template 字符串：
   class MyRenderer(BaseRenderer):
       name = "my"
       template = "📰 {chan_title}\n{time}\n{link}\n──\n{title}\n{description}"
       max_desc = 300

2. 完全控制 — 重写 render_item / send：
   class MyRenderer(BaseRenderer):
       name = "my"
       async def render_item(self, item):
           ...  # 自己构造组件列表

插件启动时自动发现 models/ 目录下所有 BaseRenderer 子类。
"""
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain

# 模板可用变量（花括号里写这些）：
# {chan_title} {title} {link} {time} {description}


class BaseRenderer:
    """渲染器基类"""

    name: str = "base"
    template: str | None = None
    max_desc: int = 500
    show_images: bool = True

    def __init__(self, builder):
        self._builder = builder

    # ── 子类可重写 ───────────────────────────────────────

    def _format_context(self, item) -> dict:
        """返回模板变量。子类可重写添加自定义变量（如 {prefix}）。"""
        return dict(
            chan_title=item.chan_title or "",
            title=item.title or "",
            link=item.link or "",
            time=self._builder.format_time(item),
            description=(item.description or "")[:self.max_desc].replace("#", "\n#"),
        )

    async def render_item(self, item) -> list:
        """渲染单条为组件列表。有 template → 模板模式，否则 → 默认排版。"""
        if self.template is not None:
            ctx = self._format_context(item)
            text = self.template.format(**ctx)
            comps = [Comp.Plain(text)]
            if self.show_images:
                comps.extend(await self._builder.fetch_images(item))
            return comps
        return await self._builder.build_chain(item)

    async def send(self, context, user: str, items: list, t2i: bool) -> int:
        """逐条发送。返回最大 pubDate_timestamp。"""
        max_ts = 0
        for item in items:
            comps = await self.render_item(item)
            msc = MessageChain(chain=comps, use_t2i_=t2i)
            await context.send_message(user, msc)
            max_ts = max(max_ts, item.pubDate_timestamp)
        return max_ts
