# 自定义推送排版

`models/` 目录下的每个 `.py` 文件就是一个**排版模型**，控制 RSS 消息推送到 QQ 时的样子。

---

## 快速上手（模板模式）

最简单的方式：写一个模板字符串。例如极简风格——只要标题和链接：

```python
# src/models/minimal.py
from .base import BaseRenderer

class MinimalRenderer(BaseRenderer):
    name = "minimal"
    template = "📌 {title}\n🔗 {link}"
```

保存后重启插件，订阅时指定：

```
/rss add-url https://example.com/feed.xml 0 * * * * minimal
```

---

## 模板变量

`{chan_title}`  频道名（如 "Twitter @肉丸"）
`{title}`       条目标题
`{link}`        原文链接
`{time}`        发布时间（东八区，格式 YYYY-MM-DD HH:MM:SS）
`{description}` 正文内容

---

## 常用配置

```python
class MyRenderer(BaseRenderer):
    name = "my"

    # 排版模板
    template = "📰 {chan_title}\n{time}\n──\n{title}\n{description}"

    # 正文最大长度（默认 500）
    max_desc = 200

    # 是否显示图片（默认 True）
    show_images = False
```

---

## 内置模型参考

### default — 通用排版
```
📰 【频道名】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🕒 2026-07-27 23:03:00
🔗 https://...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📌 标题
💬 正文（最多 500 字）
🖼️ 图片
```

### twitter — 推特专用
```
🔁 🐦 Twitter @用户名
🕒 2026-07-27 23:03:00
🔗 https://x.com/...
──────────────────────────────
💬 正文（最多 300 字，自动去掉标题）
🖼️ 图片
```

### compose — QQ 合并转发
多条 RSS 打包成一条转发消息，不刷屏。

---

## 进阶：动态内容

如果模板里需要动态决定的内容（比如根据条件显示不同标记），重写 `_format_context`：

```python
class MyRenderer(BaseRenderer):
    name = "my"
    template = "{badge} 📰 {chan_title}\n{title}"

    def _format_context(self, item):
        ctx = super()._format_context(item)
        # 标题含"紧急"就加 🚨
        ctx["badge"] = "🚨" if "紧急" in item.title else ""
        return ctx
```

`_format_context` 返回一个 `dict`，key 就是模板里 `{xxx}` 的名字。调用 `super()._format_context(item)` 拿标准变量，再添加自己的。

---

## 进阶：完全自定义

如果模板不够用，直接重写 `render_item` 和 `send`：

```python
import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from .base import BaseRenderer

class MyRenderer(BaseRenderer):
    name = "my"

    async def render_item(self, item):
        # 自己构造任意组件
        return [Comp.Plain(f"自定义内容: {item.title}")]

    async def send(self, context, user, items, t2i):
        # 自己控制发送方式（如合并转发）
        nodes = [...]
        msc = MessageChain(chain=nodes, use_t2i_=t2i)
        await context.send_message(user, msc)
        return max(i.pubDate_timestamp for i in items)
```

---

## 选择规则

1. 订阅时指定了就用指定的
2. QQ 平台 + compose 开启 → `compose`
3. 以上都不满足 → `default`
