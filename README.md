# astrbot-plugin-rss

✨ 支持 RSSHub 路由和直连 URL 订阅 RSS / Atom / JSON Feed，定时推送到聊天平台。

---

## 功能

- **多源订阅** — RSSHub 路由 + 直连 URL，支持 RSS 2.0 / Atom / JSON Feed
- **交互式向导** — 所有命令支持无参数逐步引导，也支持一键快速执行
- **定时推送** — Cron 表达式 + 快捷词，APScheduler 调度，输入校验
- **暂停/恢复** — `/rss pause` 临时关闭，`/rss resume` 恢复，连续失败自动暂停
- **可定制排版** — `models/` 下新建 `.py` 即可定义推送样式（内置 default / twitter / compose）
- **智能去重** — SHA-256 指纹，同一条目不重复推送
- **图片支持** — 自动拉取 RSS 图片，Twitter 反代 & 防和谐处理
- **合并转发** — QQ 平台多条打包为转发消息
- **失败重试** — 推送失败自动重试，不可恢复错误自动跳过，连续失败自动暂停
- **域名限速** — 同域名最多 3 并发请求，友好对待上游
- **带宽节省** — ETag/304 + 60 秒内容缓存
- **Web 管理面板** — AstrBot 仪表盘内嵌，ECharts 图表，订阅/端点/历史/日志/配置一站管理
- **启动校验** — 非法配置自动修正并告警
- **测试覆盖** — 35 个单元测试，覆盖核心模块

---

## 指令参考

所有命令不加参数时自动进入**交互式引导**，加参数直接执行。

### RSSHub 端点

| 指令 | 说明 |
|---|---|
| `/rss rsshub add [url]` | 添加端点 |
| `/rss rsshub list` | 列出端点 |
| `/rss rsshub remove [idx]` | 删除端点 |

### 订阅

| 指令 | 说明 |
|---|---|
| `/rss add [idx] [route] [分] [时] [日] [月] [周] [模型]` | RSSHub 路由订阅 |
| `/rss add-url [url] [分] [时] [日] [月] [周] [模型]` | 直连 URL 订阅 |
| `/rss test [url]` | 预览 Feed |
| `/rss list` | 列出当前会话订阅 |
| `/rss get [idx]` | 手动拉取最新条目 |
| `/rss pause [idx]` | 暂停订阅 |
| `/rss resume [idx]` | 恢复订阅 |
| `/rss update [idx] [分] [时] [日] [月] [周] [模型]` | 修改 Cron 或模型 |
| `/rss remove [idx]` | 删除订阅 |

### 工具

| 指令 | 说明 |
|---|---|
| `/rss cron` | 列出 Cron 快捷词 |
| `/rss cron <表达式>` | 解释表达式含义 |
| `/rss stats` | 查看订阅/推送统计 |
| `/rss history [条数]` | 推送历史（默认 10，最多 50） |

---

## 快速开始

### 1. RSSHub 订阅

```
/rss rsshub add https://rsshub.app
/rss test /twitter/user/nikumarusuisann
/rss add 0 /twitter/user/nikumarusuisann 0 * * * *
```

也可直接 `/rss add` 进入引导。

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
/rss list        # 查看所有
/rss pause 0     # 暂停
/rss resume 0    # 恢复
/rss stats       # 统计
/rss history 20  # 最近推送
```

---

## Cron 表达式

格式：`分钟 小时 日 月 星期`（星期 0=周日）。输错当场提示。

| 示例 | 含义 |
|---|---|
| `0 * * * *` | 每小时整点 |
| `0 9 * * *` | 每天 9:00 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 9-18 * * *` | 每天 9–18 点每小时 |
| `0 9 * * 1-5` | 工作日 9:00 |

用 `/rss cron` 查看快捷词和解释任意表达式。

---

## 自定义排版

`src/models/` 下新建 `.py`，模板模式 6 行搞定：

```python
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
| `max_consecutive_failures` | int | 100 | 连续失败自动暂停阈值（5–1000） |
| `compose` | bool | true | QQ 合并转发 |
| `t2i` | bool | false | 文字转图片 |
| `is_hide_url` | bool | false | 隐藏推送链接 |
| `proxy` | string | — | 图片代理 |
| `verify_ssl` | bool | true | HTTPS 证书验证 |

**`pic_config`**：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `is_read_pic` | bool | false | 读取 RSS 图片 |
| `max_pic_item` | int | 3 | 每次最大图片数，-1 不限制 |
| `is_adjust_pic` | bool | false | 防和谐像素修改 |
| `use_twitter_reverse_proxy` | bool | false | Twitter 图片反代 |
| `twitter_reverse_proxy_domain` | string | `pbs.yurucamp.cn` | 反代域名 |

---

## Web 管理面板

AstrBot 仪表盘 → 插件页面 → RSS 订阅管理面板：

- 📊 概览页 — ECharts 柱状图 + 环形图 + 最近推送
- 📋 订阅管理 — 增删改查、暂停/恢复、Cron 预设、模型下拉选择
- 🔗 RSSHub 端点
- 📜 推送历史
- 📝 插件日志（ERROR/WARNING/INFO 过滤）
- ⚙️ 配置在线修改

---

## 项目结构

```
astrbot_plugin_rss/
├── main.py                 插件入口
├── src/
│   ├── feed_fetcher.py     RSS 获取/解析/缓存/限速
│   ├── push_mgr.py         推送调度/重试/失败暂停
│   ├── msg_builder.py      消息排版
│   ├── data_handler.py     JSON 持久化
│   ├── pic_handler.py      图片下载/反代
│   ├── rss.py              RSSItem 数据类
│   ├── web_api.py          Plugin Pages API
│   └── models/             排版模型
│       ├── default.py      通用排版
│       ├── twitter.py      Twitter 专用
│       └── compose.py      QQ 合并转发
├── pages/dashboard/        Web 仪表盘
├── tests/                  35 个单元测试
└── README.md
```

---

## 运行测试

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

---

## 注意事项

- QQ 官方平台（`qqofficial`）主动消息限制严格，不建议定时推送
- Twitter 图片需开启 `pic_config.use_twitter_reverse_proxy`
- 同域名并发请求上限 3 个，超出自动排队
- 数值配置项启动时自动校验，非法值回退默认值
- 连续推送失败达 `max_consecutive_failures` 次后自动暂停，需手动 `/rss resume`

[GitHub](https://github.com/xiaobailoves/astrbot_plugin_rss)
