# astrbot-plugin-rss

✨ 支持 RSSHub 路由 + 直连 URL 订阅 RSS / Atom / JSON Feed，定时推送，Web 面板管理。

---

## 指令参考

所有命令**带参数直接执行**，**不带参数进入交互式引导**（回复 `cancel` 退出引导）。

### RSSHub 端点

| 指令 | 说明 |
|---|---|
| `/rss rsshub add [url]` | 添加端点 |
| `/rss rsshub list` | 列出所有端点 |
| `/rss rsshub remove [idx]` | 删除端点 |

### 订阅管理

| 指令 | 说明 |
|---|---|
| `/rss add [idx] [路由] [Cron] [模型]` | RSSHub 路由订阅 |
| `/rss add-url [url] [Cron] [模型]` | 直连 URL 订阅 |
| `/rss list` | 列出当前会话所有订阅 |
| `/rss get [idx]` | 手动拉取最新条目 |
| `/rss update [idx] [Cron] [模型]` | 修改 Cron 或模型 |
| `/rss pause [idx]` | 暂停订阅 |
| `/rss resume [idx]` | 恢复订阅 |
| `/rss remove [idx]` | 删除订阅 |

### 工具

| 指令 | 说明 |
|---|---|
| `/rss test [url]` | 预览 Feed 内容（无需订阅） |
| `/rss cron` | 列出 Cron 快捷词 |
| `/rss cron <表达式>` | 解释表达式含义 |
| `/rss stats` | 查看订阅 / 推送统计 |
| `/rss history [条数]` | 推送历史（默认 10，最多 50） |
| `/rss filter-help` | 内容过滤规则说明 |
| `/rss cancel` | 退出交互式引导 |

---

## 功能

- **交互式引导** — 所有命令无参数时逐步引导填写
- **双模推送** — 时间戳判断 / GUID 多指纹去重（默认时间模式）
- **内容过滤** — 关闭 / 正则关键词黑白名单 / AI 判断三档
- **暂停 / 恢复** — 临时关掉刷屏源，连续失败自动暂停
- **可定制排版** — `models/` 下新建 `.py` 定义推送样式（内置 default / twitter / compose）
- **站点解析** — `parse_item` 钩子提取 HTML 描述中的隐藏数据
- **失败重试** — 推送失败自动重试，不可恢复错误跳过
- **域名限速** — 同域名最多 3 并发
- **Web 面板** — ECharts 图表 + 订阅管理 + 端点 + 历史 + 日志 + 配置
- **启动校验** — 非法配置自动修正并告警
- **测试覆盖** — 36 个单元测试

---

## 快速开始

```
/rss rsshub add https://rsshub.app
/rss add  ← 进入引导，逐步填写
/rss list
/rss stats
```

---

## Cron 表达式

格式：`分钟 小时 日 月 星期`（星期 0=周日）。输错当场提示。

| 示例 | 含义 |
|---|---|
| `0 * * * *` | 每小时 |
| `0 9 * * *` | 每天 9:00 |
| `*/30 * * * *` | 每 30 分钟 |
| `0 9 * * 1-5` | 工作日 9:00 |

用 `/rss cron` 查看快捷词和解释任意表达式。

---

## 自定义排版

`src/models/` 下新建 `.py`，模板 6 行：

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
| `title_max_length` | int | 30 | 标题最大长度 |
| `description_max_length` | int | 500 | 正文最大长度 |
| `max_items_per_poll` | int | 3 | 每次最大条目数，-1 不限制 |
| `max_consecutive_failures` | int | 100 | 连续失败自动暂停阈值 |
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

## 注意事项

- QQ 官方平台主动消息限制严格，不建议定时推送
- Twitter 图片需开启 `pic_config.use_twitter_reverse_proxy`
- 同域名并发请求上限 3 个
- 数值配置项启动时自动校验

[GitHub](https://github.com/xiaobailoves/astrbot_plugin_rss)
