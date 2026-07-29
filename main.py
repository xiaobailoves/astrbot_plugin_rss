import asyncio
import logging
import re
import time
from collections import deque

import aiohttp

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp

from .src.data_handler import DataHandler
from .src.feed_fetcher import FeedFetcher
from .src.msg_builder import MessageBuilder
from .src.pic_handler import RssImageHandler
from .src.push_mgr import PushManager
from .src.models import discover_renderers
from .src.rss import RSSItem
from .src.web_api import RssWebApi


class MemoryLogHandler(logging.Handler):
    """内存日志收集器，保留最近 N 条记录供 Web 面板查看"""
    def __init__(self, capacity=300):
        super().__init__()
        self.records: deque[logging.LogRecord] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord):
        if not hasattr(record, 'plugin_tag'):
            record.plugin_tag = 'RSS'
        self.records.append(record)

    def list(self, level: str = "", count: int = 100):
        """返回日志列表，可按级别过滤"""
        result = []
        for r in self.records:
            if level and r.levelname != level.upper():
                continue
            result.append({
                "time": time.strftime("%m-%d %H:%M:%S", time.localtime(r.created)),
                "level": r.levelname,
                "msg": r.getMessage(),
            })
        return result[-count:]


@register(
    "astrbot_plugin_rss",
    "Xiaobailoves",
    "RSS订阅插件",
    "1.2.0",
    "https://github.com/xiaobailoves/astrbot_plugin_rss",
)
class RssPlugin(Star):
    # 类变量，用于存储共享实例，防止热重载时产生资源泄漏（幽灵任务）
    _shared_scheduler = None
    _shared_http_session = None

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)

        self.logger = logging.getLogger("astrbot.rss")
        self.log_handler = MemoryLogHandler()
        self.logger.addHandler(self.log_handler)
        # 交互式订阅向导状态
        self._wizards: dict[str, dict] = {}
        self.context = context
        self.config = config
        self.data_handler = DataHandler()

        # ── 提取配置 ──────────────────────────────────────
        self.title_max_length = config.get("title_max_length", 30)
        self.description_max_length = config.get("description_max_length", 500)
        self.max_items_per_poll = config.get("max_items_per_poll", 3)
        self.t2i = config.get("t2i")
        self.is_hide_url = config.get("is_hide_url")
        self.max_consecutive_failures = config.get("max_consecutive_failures", 100)
        self.is_compose = config.get("compose")
        self.proxy = config.get("proxy", None)
        self.verify_ssl = config.get("verify_ssl", True)

        # 图片配置
        pic_config = config.get("pic_config", {})
        if pic_config is None:
            pic_config = {}
        self.is_read_pic = pic_config.get("is_read_pic", False)
        self.is_adjust_pic = pic_config.get("is_adjust_pic", False)
        self.max_pic_item = pic_config.get("max_pic_item", 3)
        self.use_twitter_reverse_proxy = pic_config.get("use_twitter_reverse_proxy", False)
        self.twitter_reverse_proxy_domain = pic_config.get(
            "twitter_reverse_proxy_domain", "pbs.yurucamp.cn"
        )

        # ── 启动时配置校验 ────────────────────────────────
        defaults = {
            "title_max_length": 30,
            "description_max_length": 500,
            "max_items_per_poll": 3,
            "max_pic_item": 3,
        }
        ranges = {
            "title_max_length": (1, 200),
            "description_max_length": (1, 10000),
            "max_items_per_poll": (-1, 100),
            "max_pic_item": (-1, 50),
        }
        for key, (lo, hi) in ranges.items():
            val = getattr(self, key, None)
            if val is None or val == -1:
                continue
            if not isinstance(val, (int, float)) or val < lo or val > hi:
                self.logger.warning(
                    f"⚠️ 配置项 {key} 的值 {val} 不合法（允许范围: {lo}~{hi}），"
                    f"已自动修正为默认值 {defaults[key]}"
                )
                setattr(self, key, defaults[key])

        # ── HTTP Session ──────────────────────────────────
        self.http_session = aiohttp.ClientSession(
            trust_env=False,
            connector=aiohttp.TCPConnector(ssl=False if not self.verify_ssl else None),
            timeout=aiohttp.ClientTimeout(total=45, connect=15),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )

        # ── 子模块 ────────────────────────────────────────
        self.pic_handler = RssImageHandler(
            self.is_adjust_pic,
            proxy=self.proxy,
            use_twitter_reverse_proxy=self.use_twitter_reverse_proxy,
            twitter_reverse_proxy_domain=self.twitter_reverse_proxy_domain,
            session=self.http_session,
        )

        self.fetcher = FeedFetcher(
            http_session=self.http_session,
            data_handler=self.data_handler,
            title_max_length=self.title_max_length,
            description_max_length=self.description_max_length,
            max_items_per_poll=self.max_items_per_poll,
        )

        self.builder = MessageBuilder(
            pic_handler=self.pic_handler,
            max_pic_item=self.max_pic_item,
            is_read_pic=self.is_read_pic,
            is_hide_url=self.is_hide_url,
            t2i=self.t2i,
        )

        # 渲染器注册表
        self.renderers = discover_renderers(self.builder)
        self.logger.debug(
            f"已注册 {len(self.renderers)} 个渲染器: {list(self.renderers.keys())}"
        )

        self.push_mgr = PushManager(
            context=self.context,
            data_handler=self.data_handler,
            fetcher=self.fetcher,
            builder=self.builder,
            renderers=self.renderers,
            is_compose=self.is_compose,
            t2i=self.t2i,
            max_items_per_poll=self.max_items_per_poll,
            max_consecutive_failures=self.max_consecutive_failures,
        )

        # ── 热重载资源清理 ────────────────────────────────
        if RssPlugin._shared_scheduler is not None and RssPlugin._shared_scheduler.running:
            try:
                RssPlugin._shared_scheduler.shutdown(wait=False)
            except Exception as e:
                self.logger.warning(f"关闭旧调度器时出现异常: {e}")

        if RssPlugin._shared_http_session is not None and not RssPlugin._shared_http_session.closed:
            close_fut = asyncio.ensure_future(RssPlugin._shared_http_session.close())
            close_fut.add_done_callback(RssPlugin._log_future_error)

        RssPlugin._shared_scheduler = AsyncIOScheduler()
        self.scheduler = RssPlugin._shared_scheduler
        self.scheduler.start()
        self.scheduler.remove_all_jobs()  # 清空可能残留的 job
        RssPlugin._shared_http_session = self.http_session

        # ── WebUI 配置覆盖加载 ────────────────────────────
        saved_overrides = self.data_handler.data.get("settings", {}).get("config", {})
        for k, v in saved_overrides.items():
            self.config[k] = v

        self._fresh_asyncIOScheduler()

        # ── 注册 Plugin Pages API ─────────────────────────
        self.web_api = RssWebApi(self)
        self.web_api.register(self.context)
        self.logger.info("RSS Plugin Pages API 已注册")

    # ═══════════════════════════════════════════════════════════
    #  Scheduling
    # ═══════════════════════════════════════════════════════════

    # Cron 快捷词
    CRON_SHORTCUTS = {
        "每小时":   "0 * * * *",
        "每30分钟": "*/30 * * * *",
        "每15分钟": "*/15 * * * *",
        "每2小时":  "0 */2 * * *",
        "每6小时":  "0 */6 * * *",
        "每12小时": "0 */12 * * *",
        "每天9点":  "0 9 * * *",
        "每天":     "0 9 * * *",
        "工作日9点": "0 9 * * 1-5",
        "每周一":   "0 9 * * 1",
    }

    def normalize_cron(self, text: str) -> str:
        """将快捷词或原始 cron 转为标准 5 字段表达式，非法时抛 ValueError"""
        text = text.strip()
        if text in self.CRON_SHORTCUTS:
            return self.CRON_SHORTCUTS[text]
        fields = text.split()
        if len(fields) != 5:
            raise ValueError(
                f"无效的 Cron 表达式: '{text}'（需要 5 个字段，当前 {len(fields)} 个）\n"
                f"快捷词: {', '.join(self.CRON_SHORTCUTS)}"
            )
        # 简单合法性检查：每字段只能是 * / 数字 逗号 横线
        for i, f in enumerate(fields):
            if not re.match(r'^[\d*,/\-]+$', f):
                raise ValueError(f"Cron 第 {i+1} 个字段 '{f}' 包含非法字符")
        return text

    def parse_cron_expr(self, cron_expr: str):
        fields = self.normalize_cron(cron_expr).split()
        return {
            "minute": fields[0],
            "hour": fields[1],
            "day": fields[2],
            "month": fields[3],
            "day_of_week": fields[4],
        }

    def _add_single_job(self, url: str, user: str, cron_expr: str):
        job_id = f"{url}_{user}"
        self.scheduler.add_job(
            self.push_mgr.execute,
            "cron",
            **self.parse_cron_expr(cron_expr),
            args=[url, user],
            id=job_id,
            replace_existing=True,
        )

    def _remove_single_job(self, url: str, user: str):
        job_id = f"{url}_{user}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    def _fresh_asyncIOScheduler(self):
        self.logger.info("🔄 刷新定时任务")
        self.scheduler.remove_all_jobs()
        for url, info in self.data_handler.data.items():
            if url in ["rsshub_endpoints", "settings"] or url.startswith("_"):
                continue
            if "subscribers" not in info:
                continue
            for user, sub_info in info["subscribers"].items():
                self._add_single_job(url, user, sub_info["cron_expr"])

    # ═══════════════════════════════════════════════════════════
    #  Subscription logic
    # ═══════════════════════════════════════════════════════════

    async def _add_subscription(self, url: str, cron_expr: str, user: str,
                                  renderer: str = None):
        """纯逻辑：添加订阅，返回 (ok, data_or_error)"""
        async with self.fetcher.get_lock(url):
            subscriber = {
                "cron_expr": cron_expr,
                "last_update": 0,
                "latest_link": "",
                "pushed_hashes": [],
            }
            if renderer:
                subscriber["renderer"] = renderer

            if url in self.data_handler.data:
                if user in self.data_handler.data[url]["subscribers"]:
                    existing = self.data_handler.data[url]["subscribers"][user]
                    existing["cron_expr"] = cron_expr
                    if renderer: existing["renderer"] = renderer
                else:
                    latest_item = await self.fetcher.poll(url)
                    subscriber["last_update"] = latest_item[-1].pubDate_timestamp if latest_item else int(time.time())
                    subscriber["latest_link"] = latest_item[-1].link if latest_item else ""
                    self.data_handler.data[url]["subscribers"][user] = subscriber
            else:
                try:
                    text = await self.fetcher.fetch(url)
                    if text is None:
                        return (False, f"无法访问该 Feed，请检查网络或 URL: {url}")
                    title, desc = self.data_handler.parse_channel_text_info(text)
                    latest_item = await self.fetcher.poll(url, raw_text=text)
                except asyncio.TimeoutError:
                    return (False, f"请求超时，服务器响应过慢: {url}")
                except Exception as e:
                    return (False, f"获取 Feed 失败: {type(e).__name__}: {e}")

                subscriber["last_update"] = latest_item[-1].pubDate_timestamp if latest_item else int(time.time())
                subscriber["latest_link"] = latest_item[-1].link if latest_item else ""

                self.data_handler.data[url] = {
                    "subscribers": {user: subscriber},
                    "info": {"title": title, "description": desc},
                }
            await self.data_handler.save_data()
            return (True, self.data_handler.data[url]["info"])

    async def _add_url(self, url: str, cron_expr: str, message: AstrMessageEvent,
                       renderer: str = None):
        """命令包装：调用 _add_subscription，错误转为 plain_result"""
        ok, data = await self._add_subscription(url, cron_expr, message.unified_msg_origin, renderer)
        if not ok:
            return message.plain_result(data)
        return data

    # ═══════════════════════════════════════════════════════════
    #  Utilities
    # ═══════════════════════════════════════════════════════════

    def parse_rss_url(self, url: str) -> str:
        """确保 URL 以 http:// 或 https:// 开头"""
        return self.fetcher.normalize_url(url)

    def _is_url_or_ip(self, text: str) -> bool:
        url_pattern = r"^(?:http|https)://.+$"
        ip_pattern = (
            r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(:\d+)?$"
        )
        return bool(re.match(url_pattern, text) or re.match(ip_pattern, text))

    @staticmethod
    def _log_future_error(fut):
        try:
            exc = fut.exception()
            if exc:
                logging.getLogger("astrbot").error(f"后台任务失败: {exc}")
        except asyncio.InvalidStateError:
            pass

    async def close(self):
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if not self.http_session.closed:
            await self.http_session.close()
        if RssPlugin._shared_scheduler is self.scheduler:
            RssPlugin._shared_scheduler = None
        if RssPlugin._shared_http_session is self.http_session:
            RssPlugin._shared_http_session = None

    # ═══════════════════════════════════════════════════════════
    #  Commands: RSSHub endpoints
    # ═══════════════════════════════════════════════════════════

    @filter.command_group("rss", alias={"RSS"})
    def rss(self):
        pass

    @rss.group("rsshub")
    def rsshub(self, event: AstrMessageEvent):
        pass

    @rsshub.command("add")
    async def rsshub_add(self, event: AstrMessageEvent, url: str = None):
        """添加 RSSHub 端点"""
        """添加 RSSHub 端点"""
        if url is None:
            self._wizards[event.unified_msg_origin] = {"step": "ep_add_url"}
            yield event.plain_result("🔗 输入 RSSHub 端点 URL:")
            return
        if url.endswith("/"):
            url = url[:-1]
        url = self.parse_rss_url(url)
        if not self._is_url_or_ip(url):
            yield event.plain_result("请输入正确的URL")
            return
        elif url in self.data_handler.data.get("rsshub_endpoints", []):
            yield event.plain_result("该RSSHub端点已存在")
            return
        else:
            self.data_handler.data.get("rsshub_endpoints", []).append(url)
            await self.data_handler.save_data()
            yield event.plain_result("添加成功")

    @rsshub.command("list")
    async def rsshub_list(self, event: AstrMessageEvent):
        """列出所有 RSSHub 端点"""
        """列出所有 RSSHub 端点"""
        ret = "当前Bot添加的rsshub endpoint：\n"
        yield event.plain_result(
            ret
            + "\n".join(
                [
                    f"{i}: {x}"
                    for i, x in enumerate(self.data_handler.data.get("rsshub_endpoints", []))
                ]
            )
        )

    @rsshub.command("remove")
    async def rsshub_remove(self, event: AstrMessageEvent, idx: int = None):
        """删除 RSSHub 端点"""
        """删除 RSSHub 端点"""
        eps = self.data_handler.data.get("rsshub_endpoints", [])
        if idx is None:
            if not eps:
                yield event.plain_result("暂无端点")
                return
            lines = ["🔗 选择要删除的端点（输入序号）:"]
            for i, u in enumerate(eps):
                lines.append(f"  {i}. {u}")
            yield event.plain_result("\n".join(lines))
            self._wizards[event.unified_msg_origin] = {"step": "ep_rm_pick"}
            return
        if idx < 0 or idx >= len(eps):
            yield event.plain_result("索引越界")
            return
        else:
            self.data_handler.data.get("rsshub_endpoints", []).pop(idx)
            await self.data_handler.save_data()
            self._fresh_asyncIOScheduler()
            yield event.plain_result("删除成功")

    # ═══════════════════════════════════════════════════════════
    #  Commands: Subscriptions
    # ═══════════════════════════════════════════════════════════

    async def _handle_wizard(self, event: AstrMessageEvent):
        """处理交互式向导的消息（add / add-url / update）"""
        user = event.unified_msg_origin
        w = self._wizards.get(user)
        if not w:
            return
        text = event.message_str.strip()

        # ── 共享步骤: cron ──
        if w["step"] == "cron":
            try:
                cron = self.normalize_cron(text)
            except ValueError as e:
                yield event.plain_result(f"格式错误: {e}\n请重新输入:")
                return
            w["cron"] = cron
            w["step"] = "model"
            yield event.plain_result("🎨 输入模型 (默认/twitter/compose，直接回车跳过):")
            return

        # ── 共享步骤: model ──
        if w["step"] == "model":
            model = text.strip() if text.strip() else None
            cmd = w.get("cmd", "add")
            if cmd == "add-url":
                url = self.fetcher.normalize_url(w["url"])
                ret = await self._add_url(url, w["cron"], event, model)
                if isinstance(ret, MessageEventResult):
                    yield ret
                else:
                    self._add_single_job(url, user, w["cron"])
                    yield event.plain_result(
                        f"✅ 添加成功！\n频道: {ret['title']}\n周期: `{w['cron']}`\n模型: {model or '默认'}"
                    )
            elif cmd == "update":
                idx = w["idx"]
                subs = self.data_handler.get_subs_channel_url(user)
                if idx < 0 or idx >= len(subs):
                    yield event.plain_result("该订阅已不存在，请重新操作")
                    del self._wizards[user]
                    return
                url = subs[idx]
                sub = self.data_handler.data[url]["subscribers"][user]
                sub["cron_expr"] = w["cron"]
                if model:
                    sub["renderer"] = model
                elif "renderer" in sub:
                    del sub["renderer"]
                await self.data_handler.save_data()
                self._remove_single_job(url, user)
                self._add_single_job(url, user, w["cron"])
                yield event.plain_result(
                    f"✅ 更新成功！\n频道: {self.data_handler.data[url]['info']['title']}\n周期: `{w['cron']}`"
                )
            else:  # add (rsshub)
                url = self.data_handler.data.get("rsshub_endpoints", [])[w["idx"]] + w["route"]
                ret = await self._add_url(url, w["cron"], event, model)
                if isinstance(ret, MessageEventResult):
                    yield ret
                else:
                    self._add_single_job(url, user, w["cron"])
                    yield event.plain_result(
                        f"✅ 添加成功！\n频道: {ret['title']}\n周期: `{w['cron']}`\n模型: {model or '默认'}"
                    )
            del self._wizards[user]
            return

        # ── add/rsshub 专有: idx → route ──
        if w["step"] == "idx":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            eps = self.data_handler.data.get("rsshub_endpoints", [])
            if i < 0 or i >= len(eps):
                yield event.plain_result("序号超出范围")
                return
            w["idx"] = i
            w["step"] = "route"
            yield event.plain_result("🔗 输入路由 (如 /twitter/user/xxx):")
            return

        if w["step"] == "route":
            if not text.startswith("/"):
                yield event.plain_result("路由必须以 / 开头，请重新输入:")
                return
            w["route"] = text
            w["step"] = "cron"
            yield event.plain_result("⏱ 输入 Cron 表达式 (如 0 * * * *，快捷词如 每小时):")
            return

        # ── add-url 专有: url ──
        if w["step"] == "url":
            if not text:
                yield event.plain_result("URL 不能为空，请重新输入:")
                return
            w["url"] = text
            w["step"] = "cron"
            yield event.plain_result("⏱ 输入 Cron 表达式 (如 0 * * * *，快捷词如 每小时):")
            return

        # ── update 专有: 选订阅 ──
        if w["step"] == "pick":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            subs = self.data_handler.get_subs_channel_url(user)
            if i < 0 or i >= len(subs):
                yield event.plain_result("序号超出范围")
                return
            w["idx"] = i
            w["step"] = "cron"
            yield event.plain_result("⏱ 输入新 Cron 表达式 (如 0 * * * *，快捷词如 每小时):")
            return

        # ── remove 专有: 选 → 确认 ──
        if w["step"] == "rm_pick":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            subs = self.data_handler.get_subs_channel_url(user)
            if i < 0 or i >= len(subs):
                yield event.plain_result("序号超出范围")
                return
            w["idx"] = i
            w["step"] = "rm_confirm"
            title = self.data_handler.data[subs[i]]["info"]["title"]
            yield event.plain_result(f"确定删除【{title}】？(y/n)")
            return

        if w["step"] == "rm_confirm":
            if text.lower() in ("y", "yes", "是"):
                idx = w["idx"]
                subs = self.data_handler.get_subs_channel_url(user)
                url = subs[idx]
                self.data_handler.data[url]["subscribers"].pop(user, None)
                if not self.data_handler.data[url]["subscribers"]:
                    self.data_handler.data.pop(url)
                self._remove_single_job(url, user)
                await self.data_handler.save_data()
                yield event.plain_result("✅ 已删除")
            else:
                yield event.plain_result("已取消")
            del self._wizards[user]
            return

        # ── get 专有: 选 → 展示 ──
        if w["step"] == "get_pick":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            subs = self.data_handler.get_subs_channel_url(user)
            if i < 0 or i >= len(subs):
                yield event.plain_result("序号超出范围")
                return
            url = subs[i]
            async with self.fetcher.get_lock(url):
                items = await self.fetcher.poll(url)
            if not items:
                yield event.plain_result("📭 暂无内容")
            else:
                item = items[-1]
                comps = await self.builder.build_chain(item)
                yield event.chain_result(comps).use_t2i(self.t2i)
            del self._wizards[user]
            return

        # ── pause 专有: 选 → 暂停 ──
        if w["step"] == "pause_pick":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            subs = self.data_handler.get_subs_channel_url(user)
            if i < 0 or i >= len(subs):
                yield event.plain_result("序号超出范围")
                return
            url = subs[i]
            self.data_handler.data[url]["subscribers"][user]["paused"] = True
            await self.data_handler.save_data()
            title = self.data_handler.data[url]["info"]["title"]
            yield event.plain_result(f"⏸️ 已暂停: {title}")
            del self._wizards[user]
            return

        # ── resume 专有: 选已暂停 → 恢复 ──
        if w["step"] == "resume_pick":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            subs = self.data_handler.get_subs_channel_url(user)
            paused = [(j, u) for j, u in enumerate(subs)
                      if self.data_handler.data[u]["subscribers"][user].get("paused")]
            if i < 0 or i >= len(paused):
                yield event.plain_result("序号超出范围")
                return
            _, url = paused[i]
            self.data_handler.data[url]["subscribers"][user]["paused"] = False
            await self.data_handler.save_data()
            title = self.data_handler.data[url]["info"]["title"]
            yield event.plain_result(f"▶️ 已恢复: {title}")
            del self._wizards[user]
            return

        # ── test 专有: 输入 URL → 预览 ──
        if w["step"] == "test_url":
            url = self.fetcher.normalize_url(text)
            yield event.plain_result(f"🔍 正在获取 {url} ...")
            try:
                raw = await self.fetcher.fetch(url)
                if raw is None:
                    yield event.plain_result("❌ 无法访问该 Feed")
                else:
                    title, desc = self.data_handler.parse_channel_text_info(raw)
                    items = await self.fetcher.poll(url, num=3, raw_text=raw)
                    lines = [f"📰 {title}", f"📝 {desc[:200]}", f"━━ 预览最新 {len(items)} 条 ━━"]
                    for item in items:
                        t = self.builder.format_time(item)
                        time_str = f"  [{t}] " if t else "  "
                        lines.append(f"{time_str}📌 {item.title[:60]}")
                    yield event.plain_result("\n".join(lines))
            except Exception as e:
                yield event.plain_result(f"❌ 解析失败: {e}")
            del self._wizards[user]
            return

        # ── rsshub add 专有: 输入 URL → 添加 ──
        if w["step"] == "ep_add_url":
            url = self.fetcher.normalize_url(text)
            if url in self.data_handler.data.get("rsshub_endpoints", []):
                yield event.plain_result("该端点已存在")
            else:
                self.data_handler.data.get("rsshub_endpoints", []).append(url)
                await self.data_handler.save_data()
                yield event.plain_result(f"✅ 已添加: {url}")
            del self._wizards[user]
            return

        # ── rsshub remove 专有: 选端点 → 删 ──
        if w["step"] == "ep_rm_pick":
            try:
                i = int(text)
            except ValueError:
                yield event.plain_result("请输入数字序号")
                return
            eps = self.data_handler.data.get("rsshub_endpoints", [])
            if i < 0 or i >= len(eps):
                yield event.plain_result("序号超出范围")
                return
            eps.pop(i)
            await self.data_handler.save_data()
            self._fresh_asyncIOScheduler()
            yield event.plain_result("✅ 端点已删除")
            del self._wizards[user]
            return

    @filter.command("")
    async def _wizard_handler(self, event: AstrMessageEvent):
        """[内部] 处理交互式向导消息"""
        user = event.unified_msg_origin
        # 清理过期向导（超过 10 分钟未活动）
        now = time.time()
        stale = [u for u, w in self._wizards.items()
                  if now - w.get("_ts", 0) > 600]
        for u in stale:
            del self._wizards[u]
        if user not in self._wizards:
            return  # 不在向导中，忽略
        self._wizards[user]["_ts"] = now
        async for result in self._handle_wizard(event):
            yield result

    @rss.command("add")
    async def add_command(
        self, event: AstrMessageEvent,
        idx: int = None, route: str = None,
        minute: str = None, hour: str = None, day: str = None,
        month: str = None, day_of_week: str = None,
        renderer: str = None,
    ):
        """通过 RSSHub 路由添加订阅（无参数进入引导）"""
        """通过 RSSHub 路由添加订阅（无参数进入引导）"""
        # 在向导中？路由到向导处理
        if event.unified_msg_origin in self._wizards:
            async for result in self._handle_wizard(event):
                yield result
            return

        # 无参数 → 启动交互式向导
        if idx is None:
            eps = self.data_handler.data.get("rsshub_endpoints", [])
            if not eps:
                yield event.plain_result("请先添加 RSSHub 端点: /rss rsshub add <url>")
                return
            lines = ["📡 选择一个 RSSHub 端点（输入序号）:"]
            for i, u in enumerate(eps):
                lines.append(f"  {i}. {u}")
            lines.append("── 也可以直接 /rss add <序号> <路由> <Cron> [模型] 快速订阅")
            yield event.plain_result("\n".join(lines))
            self._wizards[event.unified_msg_origin] = {"step": "idx", "type": "rsshub"}
            return

        if idx < 0 or idx >= len(self.data_handler.data.get("rsshub_endpoints", [])):
            yield event.plain_result("索引越界")
            return
        if not route or not route.startswith("/"):
            yield event.plain_result("路由必须以 / 开头")
            return
        if not minute:
            yield event.plain_result("缺少 Cron 参数")
            return

        url = self.data_handler.data.get("rsshub_endpoints", [])[idx] + route
        cron_expr = f"{minute or '*'} {hour or '*'} {day or '*'} {month or '*'} {day_of_week or '*'}"
        try:
            cron_expr = self.normalize_cron(cron_expr)
        except ValueError as e:
            yield event.plain_result(f"❌ Cron 格式错误: {e}")
            return

        ret = await self._add_url(url, cron_expr, event, renderer)
        if isinstance(ret, MessageEventResult):
            yield ret
            return
        else:
            chan_title = ret["title"]
            chan_desc = ret["description"]

        self._add_single_job(url, event.unified_msg_origin, cron_expr)
        yield event.plain_result(
            f"添加成功。频道信息：\n标题: {chan_title}\n描述: {chan_desc}"
        )

    @rss.command("add-url")
    async def add_url_command(
        self, event: AstrMessageEvent,
        url: str = None,
        minute: str = None, hour: str = None, day: str = None,
        month: str = None, day_of_week: str = None,
        renderer: str = None,
    ):
        """通过直连 URL 添加订阅（无参数进入引导）"""
        if url is None:
            self._wizards[event.unified_msg_origin] = {"step": "url", "cmd": "add-url"}
            yield event.plain_result("🔗 输入 RSS Feed URL:\n── 也可以直接 /rss add-url <url> <Cron> [模型] 快速订阅")
            return
        url = self.parse_rss_url(url)
        cron_expr = f"{minute} {hour} {day} {month} {day_of_week}"
        ret = await self._add_url(url, cron_expr, event, renderer)
        if isinstance(ret, MessageEventResult):
            yield ret
            return
        else:
            chan_title = ret["title"]
            chan_desc = ret["description"]

        self._add_single_job(url, event.unified_msg_origin, cron_expr)
        yield event.plain_result(
            f"添加成功。频道信息：\n标题: {chan_title}\n描述: {chan_desc}"
        )

    @rss.command("list")
    async def list_command(self, event: AstrMessageEvent):
        """列出当前会话所有订阅"""
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if not subs_urls:
            yield event.plain_result("当前没有任何订阅。")
            return
        ret = "📋 当前订阅列表：\n"
        for i, url in enumerate(subs_urls):
            info = self.data_handler.data[url]["info"]
            sub = self.data_handler.data[url]["subscribers"][user]
            cron = sub.get("cron_expr", "* * * * *")
            rdr = sub.get("renderer", "")
            paused = "⏸️ " if sub.get("paused") else ""
            line = f"{i}. {paused}【{info['title']}】\n   周期: `{cron}`"
            if rdr:
                line += f"  渲染: `{rdr}`"
            ret += line + "\n"
        yield event.plain_result(ret.strip())

    @rss.command("remove")
    async def remove_command(self, event: AstrMessageEvent, idx: int = None):
        """删除指定订阅（无参数进入引导）"""
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if idx is None:
            if not subs_urls:
                yield event.plain_result("当前没有任何订阅")
                return
            lines = ["🗑 选择要删除的订阅（输入序号）:"]
            for i, url in enumerate(subs_urls):
                lines.append(f"  {i}. {self.data_handler.data[url]['info']['title']}")
            yield event.plain_result("\n".join(lines))
            self._wizards[user] = {"step": "rm_pick"}
            return
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引越界, 请使用 /rss list 查看已经添加的订阅")
            return
        url = subs_urls[idx]
        subscriber = self.data_handler.data[url]["subscribers"].pop(event.unified_msg_origin, None)
        if subscriber is None:
            yield event.plain_result("你未订阅该频道。")
            return

        if not self.data_handler.data[url]["subscribers"]:
            self.data_handler.data.pop(url)

        self._remove_single_job(url, event.unified_msg_origin)
        await self.data_handler.save_data()
        yield event.plain_result("删除成功")

    @rss.command("stats")
    async def stats_command(self, event: AstrMessageEvent):
        """查看订阅/推送统计"""
        """查看插件统计信息"""
        data = self.data_handler.data
        feeds = {k: v for k, v in data.items()
                 if k not in ("rsshub_endpoints", "settings") and not k.startswith("_")
                 and "subscribers" in v}
        total_subs = sum(len(v["subscribers"]) for v in feeds.values())
        total_feeds = len(feeds)
        paused = sum(
            1 for v in feeds.values()
            for s in v["subscribers"].values() if s.get("paused")
        )
        history = data.get("_push_history", [])
        failed = data.get("_failed_pushes", [])
        eps = len(data.get("rsshub_endpoints", []))
        jobs = len(self.scheduler.get_jobs())

        lines = [
            "📊 RSS 插件统计",
            f"━━ 订阅 ━━",
            f"  Feed 源: {total_feeds}  总订阅: {total_subs}",
            f"  暂停中: {paused}  活跃: {total_subs - paused}",
            f"  RSSHub 端点: {eps}  APScheduler 任务: {jobs}",
            f"━━ 推送 ━━",
            f"  历史记录: {len(history)}",
            f"  待重试: {len(failed)}",
        ]
        yield event.plain_result("\n".join(lines))

    @rss.command("pause")
    async def pause_command(self, event: AstrMessageEvent, idx: int = None):
        """暂停订阅（停止推送但不删除）"""
        """暂停订阅（停止推送但不删除）"""
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if idx is None:
            if not subs_urls:
                yield event.plain_result("当前没有任何订阅")
                return
            lines = ["⏸ 选择要暂停的订阅（输入序号）:"]
            for i, url in enumerate(subs_urls):
                lines.append(f"  {i}. {self.data_handler.data[url]['info']['title']}")
            yield event.plain_result("\n".join(lines))
            self._wizards[user] = {"step": "pause_pick"}
            return
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引越界")
            return
        url = subs_urls[idx]
        self.data_handler.data[url]["subscribers"][event.unified_msg_origin]["paused"] = True
        await self.data_handler.save_data()
        title = self.data_handler.data[url]["info"]["title"]
        yield event.plain_result(f"⏸️ 已暂停: {title}")

    @rss.command("resume")
    async def resume_command(self, event: AstrMessageEvent, idx: int = None):
        """恢复已暂停的订阅"""
        """恢复订阅"""
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if idx is None:
            paused = [(j, u) for j, u in enumerate(subs_urls)
                      if self.data_handler.data[u]["subscribers"][user].get("paused")]
            if not paused:
                yield event.plain_result("没有已暂停的订阅")
                return
            lines = ["▶️ 选择要恢复的订阅（输入序号）:"]
            for j, (_, url) in enumerate(paused):
                lines.append(f"  {j}. {self.data_handler.data[url]['info']['title']}")
            yield event.plain_result("\n".join(lines))
            self._wizards[user] = {"step": "resume_pick"}
            return
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引越界")
            return
        url = subs_urls[idx]
        self.data_handler.data[url]["subscribers"][event.unified_msg_origin]["paused"] = False
        await self.data_handler.save_data()
        title = self.data_handler.data[url]["info"]["title"]
        yield event.plain_result(f"▶️ 已恢复: {title}")

    @rss.command("update")
    async def update_command(
        self, event: AstrMessageEvent, idx: int = None,
        minute: str = None, hour: str = None, day: str = None,
        month: str = None, day_of_week: str = None,
        renderer: str = None,
    ):
        """修改订阅的 Cron 周期或模型（无参数进入引导）"""
        user = event.unified_msg_origin
        if idx is None:
            subs = self.data_handler.get_subs_channel_url(user)
            if not subs:
                yield event.plain_result("当前没有任何订阅")
                return
            lines = ["📋 选择要修改的订阅（输入序号）:"]
            for i, url in enumerate(subs):
                info = self.data_handler.data[url]["info"]
                lines.append(f"  {i}. {info['title']}")
            lines.append("── 也可以直接 /rss update <序号> <Cron> [模型] 快速修改")
            yield event.plain_result("\n".join(lines))
            self._wizards[user] = {"step": "pick", "cmd": "update"}
            return
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引错误。")
            return
        if not minute:
            yield event.plain_result("缺少 Cron 参数")
            return
        url = subs_urls[idx]
        new_cron = f"{minute} {hour} {day} {month} {day_of_week}"
        try:
            new_cron = self.normalize_cron(new_cron)
        except ValueError as e:
            yield event.plain_result(f"❌ Cron 格式错误: {e}")
            return
        sub = self.data_handler.data[url]["subscribers"][user]
        sub["cron_expr"] = new_cron
        if renderer:
            sub["renderer"] = renderer
        elif renderer == "" and "renderer" in sub:
            del sub["renderer"]  # 空字符串 = 恢复自动选择
        await self.data_handler.save_data()
        self._remove_single_job(url, user)
        self._add_single_job(url, user, new_cron)
        info = f"✅ 更新成功！\n频道: {self.data_handler.data[url]['info']['title']}\n新周期: `{new_cron}`"
        if renderer:
            info += f"\n渲染器: `{renderer}`"
        yield event.plain_result(info)

    @rss.command("get")
    async def get_command(self, event: AstrMessageEvent, idx: int = None):
        """手动拉取指定订阅的最新条目"""
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if idx is None:
            if not subs_urls:
                yield event.plain_result("当前没有任何订阅")
                return
            lines = ["📥 选择要查看的订阅（输入序号）:"]
            for i, url in enumerate(subs_urls):
                lines.append(f"  {i}. {self.data_handler.data[url]['info']['title']}")
            yield event.plain_result("\n".join(lines))
            self._wizards[user] = {"step": "get_pick"}
            return
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引越界, 请使用 /rss list 查看已经添加的订阅")
            return
        url = subs_urls[idx]
        async with self.fetcher.get_lock(url):
            rss_items = await self.fetcher.poll(url)
        if not rss_items:
            yield event.plain_result("没有新的订阅内容")
            return

        item = rss_items[-1]
        user_parts = event.unified_msg_origin.split(":")
        platform_name = user_parts[0] if len(user_parts) > 0 else ""

        comps = await self.builder.build_chain(item)
        if platform_name == "aiocqhttp" and self.is_compose:
            node = Comp.Node(uin=0, name="Astrbot", content=comps)
            yield event.chain_result([node]).use_t2i(self.t2i)
        else:
            yield event.chain_result(comps).use_t2i(self.t2i)

    @rss.command("history")
    async def history_command(self, event: AstrMessageEvent, count: int = 10):
        """查看最近推送历史记录"""
        from datetime import datetime, timezone, timedelta
        history = self.data_handler.data.get("_push_history", [])
        if not history:
            yield event.plain_result("暂无推送记录。")
            return
        count = max(1, min(count, 50))
        recent = history[-count:]
        tz_utc_8 = timezone(timedelta(hours=8))
        lines = [f"📋 最近 {len(recent)} 条推送记录："]
        for entry in reversed(recent):
            t = datetime.fromtimestamp(entry["time"], tz=tz_utc_8).strftime("%m-%d %H:%M")
            lines.append(f"  [{t}] 📰 {entry['chan_title'][:20]}\n       📌 {entry['title'][:40]}")
        yield event.plain_result("\n".join(lines))

    @rss.command("test")
    async def test_command(self, event: AstrMessageEvent, url: str = None):
        """预览 RSS Feed 内容（无需订阅）"""
        if url is None:
            self._wizards[event.unified_msg_origin] = {"step": "test_url"}
            yield event.plain_result("🔗 输入要预览的 RSS Feed URL:")
            return
        url = self.parse_rss_url(url)
        yield event.plain_result(f"🔍 正在获取 {url} ...")

        try:
            text = await self.fetcher.fetch(url)
            if text is None:
                yield event.plain_result(f"❌ 无法访问该 Feed，请检查 URL: {url}")
                return
            title, desc = self.data_handler.parse_channel_text_info(text)
            items = await self.fetcher.poll(url, num=3, raw_text=text)
        except Exception as e:
            yield event.plain_result(f"❌ 解析失败: {str(e)}")
            return

        if not items:
            yield event.plain_result(
                f"📭 该 Feed 暂无内容。\n📰 频道: {title}\n📝 描述: {desc[:200]}"
            )
            return

        lines = [
            f"📰 {title}",
            f"📝 {desc[:200]}",
            f"━━ 预览最新 {len(items)} 条 ━━",
        ]
        for item in items:
            t = self.builder.format_time(item)
            time_str = f"  [{t}] " if t else "  "
            lines.append(f"{time_str}📌 {item.title[:60]}")
        yield event.plain_result("\n".join(lines))

    @rss.command("cron")
    async def cron_command(self, event: AstrMessageEvent, arg: str = ""):
        """Cron 表达式帮助（无参列快捷词，有参解释含义）"""
        """Cron 帮助：无参列快捷词，有参解释表达式含义"""
        arg = arg.strip()
        if not arg:
            lines = ["⏱ Cron 快捷词（可直接替代 5 字段表达式）："]
            for word, expr in self.CRON_SHORTCUTS.items():
                lines.append(f"  {word:12s} →  `{expr}`")
            lines.append("")
            lines.append("格式：分 时 日 月 周（周 0=周日）")
            lines.append("例：`0 */2 * * *` = 每2小时  `0 9 * * 1-5` = 工作日9点")
            yield event.plain_result("\n".join(lines))
            return

        try:
            expr = self.normalize_cron(arg)
        except ValueError as e:
            yield event.plain_result(f"❌ {e}")
            return

        parts = expr.split()
        desc = []
        for name, val in [("分钟", parts[0]), ("小时", parts[1]),
                           ("日", parts[2]), ("月", parts[3]), ("星期", parts[4])]:
            if val == "*":
                desc.append(f"每{name}")
            elif val.startswith("*/"):
                desc.append(f"每{val[2:]}{name}")
            elif "-" in val:
                a, b = val.split("-", 1)
                desc.append(f"{name}={a}到{b}")
            elif "," in val:
                desc.append(f"{name}={val}")
            else:
                desc.append(f"{name}={val}")
        yield event.plain_result(f"`{expr}` → {'，'.join(desc)}")
