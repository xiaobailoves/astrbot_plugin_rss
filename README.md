# astrbot-plugin-rss

✨ 支持 RSSHub 路由和直连 URL 订阅 RSS / Atom / JSON Feed，定时推送到聊天平台。

---

## 功能

- **多源订阅** — RSSHub 路由 + 直连 URL，支持 RSS 2.0 / Atom / JSON Feed
- **定时推送** — Cron 表达式 + 快捷词控制频率，APScheduler 调度，输入校验
- **暂停/恢复** — `/rss pause` 临时关掉刷屏源，`/rss resume` 恢复
- **可定制排版** — `models/` 目录下新建 `.py` 文件即可自定义推送样式（内置 default / twitter / compose）
- **智能去重** — SHA-256 指纹，同一条目不重复推送
- **图片支持** — 自动拉取 RSS 图片，Twitter 反代 & 防和谐处理
- **合并转发** — QQ 平台多条打包为合并转发消息
- **失败重试** — 推送失败自动重试，不可恢复错误（封禁/会话不存在）自动跳过
- **域名限速** — 同域名最多 3 个并发请求，友好对待上游服务器
- **带宽节省** — ETag/304 + 60 秒 Feed 内容缓存，避免重复下载
- **统计面板** — `/rss stats` 查看订阅数、推送量、待重试数
- **Web 管理** — AstrBot 仪表盘内嵌，订阅/端点/历史/配置一站管理
- **启动校验** — 非法配置自动修正并告警

---

## 指令参考

### RSSHub 端点

| 指令 | 说明 |
|---|---|
| `/rss rsshub add <url>` | 添加端点 |
| `/rss rsshub list` | 列出端点 |
| `/rss rsshub remove <idx>` | 删除端点 |

### 订阅

| 指令 | 说明 |
|---|---|
| `/rss add <idx> <route> <分> <时> <日> <月> <周> [模型]` | RSSHub 路由订阅 |
| `/rss add-url <url> <分> <时> <日> <月> <周> [模型]` | 直连 URL 订阅 |
| `/rss test <url>` | 预览 Feed，确认内容质量 |
| `/rss list` | 列出当前会话订阅（暂停的标 ⏸️） |
| `/rss get <idx>` | 手动拉取最新条目 |
| `/rss pause <idx>` | 暂停订阅（停止推送但不删除） |
| `/rss resume <idx>` | 恢复订阅 |
| `/rss update <idx> <分> <时> <日> <月> <周> [模型]` | 修改 Cron 或模型 |
| `/rss remove <idx>` | 删除订阅 |

### 工具

| 指令 | 说明 |
|---|---|
| `/rss cron` | 列出 Cron 快捷词 |
| `/rss cron <表达式>` | 解释表达式的含义 |
| `/rss stats` | 查看订阅/推送统计 |
| `/rss history [条数]` | 推送历史（默认 10，最多 50） |

---

## 快速开始

### 1. RSSHub 订阅

```
/rss rsshub add https://rsshub.app
/rss test 0 /bilibili/user/dynamic/45678          # 先预览
/rss add 0 /bilibili/user/dynamic/45678 0 * * * * # 正式订阅
```

路由参考 [RSSHub 文档](https://docs.rsshub.app/zh/routes/popular)。

### 2. 直连 URL 订阅

```
/rss test https://blog.example.com/feed.xml
/rss add-url https://blog.example.com/feed.xml 0 * * * *
```

### 3. 指定排版模型

```
/rss add-url https://rsshub.app/twitter/user/xxx 0 * * * * twitter
/rss update 0 0 * * * * twitter
```

### 4. 日常管理

```
/rss list                 # 查看所有订阅
/rss pause 0              # 刷屏了，先暂停
/rss resume 0             # 恢复了，继续
/rss stats                # 看看整体情况
/rss history 20           # 最近 20 条推送
```

---

## Cron 表达式

格式：`分钟 小时 日 月 星期`（星期 0=周日）。输错当场提示，不会存脏数据。

| 示例 | 含义 |
|---|---|
| `0 * * * *` | 每小时整点 |
| `0 9 * * *` | 每天 9:00 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 9-18 * * *` | 每天 9–18 点每小时 |
| `0 9 * * 1-5` | 工作日 9:00 |

用 `/rss cron` 查看内置快捷词和解释任意表达式。

---

## 自定义排版

在 `src/models/` 下新建 `.py` 文件，模板模式 6 行搞定：

```python
# src/models/minimal.py
from .base import BaseRenderer

class MinimalRenderer(BaseRenderer):
    name = "minimal"
    template = "📌 {title}\n🔗 {link}"
```

订阅时指定：`/rss add-url ... minimal`

模板变量：`{chan_title}` `{title}` `{link}` `{time}` `{description}`

详细教程见 [`src/models/README.md`](src/models/README.md)。

---

## 配置项

在 AstrBot 插件管理中修改。

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `title_max_length` | int | 30 | 标题最大长度（1–200） |
| `description_max_length` | int | 500 | 正文最大长度（1–10000） |
| `max_items_per_poll` | int | 3 | 每次最大条目数，-1 不限制 |
| `compose` | bool | true | QQ 合并转发 |
| `t2i` | bool | false | 文字转图片（图片会丢失） |
| `is_hide_url` | bool | false | 隐藏推送链接 |
| `proxy` | string | — | 图片代理，如 `http://127.0.0.1:7890` |
| `verify_ssl` | bool | true | HTTPS 证书验证 |

**`pic_config`**：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `is_read_pic` | bool | false | 读取 RSS 图片 |
| `max_pic_item` | int | 3 | 每次最大图片数，-1 不限制 |
| `is_adjust_pic` | bool | false | 修改角落像素（防和谐） |
| `use_twitter_reverse_proxy` | bool | false | Twitter 图片反代 |
| `twitter_reverse_proxy_domain` | string | `pbs.yurucamp.cn` | 反代域名 |

---

## Web 管理面板

AstrBot 仪表盘 → 插件页面 → RSS 管理，支持：

- 📋 订阅增删改查、暂停/恢复、手动拉取、Cron 预设
- 🔗 RSSHub 端点管理
- 📜 推送历史查看
- 📊 统计面板
- 📝 插件日志实时查看
- ⚙️ 配置在线修改

---

## 注意事项

- QQ 官方平台（`qqofficial`）主动消息限制严格，不建议定时推送
- Twitter 图片需开启 `pic_config.use_twitter_reverse_proxy`
- 同域名并发请求上限 3 个，超出自动排队
- 数值配置项启动时自动校验，非法值回退默认值

[GitHub](https://github.com/xiaobailoves/astrbot_plugin_rss)
