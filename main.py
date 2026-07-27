import aiohttp
import asyncio
import calendar
import feedparser
import hashlib
import time
import re
from datetime import datetime, timezone, timedelta
import email.utils
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult,MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp

from .data_handler import DataHandler
from .pic_handler import RssImageHandler
from .rss import RSSItem
from .webui import RssWebUI
from typing import List


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

        self.logger = logging.getLogger("astrbot")
        self.context = context
        self.config = config
        self.data_handler = DataHandler()

        # ETag 缓存：避免重复下载未更新的 feed
        self._feed_etags = {}

        # 提取scheme文件中的配置
        self.title_max_length = config.get("title_max_length")
        self.description_max_length = config.get("description_max_length")
        self.max_items_per_poll = config.get("max_items_per_poll")
        self.t2i = config.get("t2i")
        self.is_hide_url = config.get("is_hide_url")
        self.is_compose = config.get("compose")
        self.proxy = config.get("proxy", None) # 新增代理配置
        self.verify_ssl = config.get("verify_ssl", True)

        # 提取图片配置 (兼容旧版本配置可能不存在新 key 的情况)
        pic_config = config.get("pic_config", {})
        if pic_config is None:
            pic_config = {}
        self.is_read_pic = pic_config.get("is_read_pic", False)
        self.is_adjust_pic = pic_config.get("is_adjust_pic", False)
        self.max_pic_item = pic_config.get("max_pic_item", 3)
        self.use_twitter_reverse_proxy = pic_config.get("use_twitter_reverse_proxy", False)
        self.twitter_reverse_proxy_domain = pic_config.get("twitter_reverse_proxy_domain", "pbs.yurucamp.cn")

        # 复用 HTTP Session，避免每次请求都重新建立连接
        self.http_session = aiohttp.ClientSession(
            trust_env=False,
            connector=aiohttp.TCPConnector(ssl=False if not self.verify_ssl else None),
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )

        # 传入代理配置给图片处理器，共享 HTTP Session
        self.pic_handler = RssImageHandler(
            self.is_adjust_pic,
            proxy=self.proxy,
            use_twitter_reverse_proxy=self.use_twitter_reverse_proxy,
            twitter_reverse_proxy_domain=self.twitter_reverse_proxy_domain,
            session=self.http_session
        )
        
        # --- 修复热重载时资源泄漏 ---
        if RssPlugin._shared_scheduler is not None and RssPlugin._shared_scheduler.running:
            try:
                RssPlugin._shared_scheduler.shutdown(wait=False)
            except Exception as e:
                self.logger.warning(f"关闭旧调度器时出现异常: {e}")

        if RssPlugin._shared_http_session is not None and not RssPlugin._shared_http_session.closed:
            close_fut = asyncio.ensure_future(RssPlugin._shared_http_session.close())
            close_fut.add_done_callback(RssPlugin._log_future_error)

        # 创建新的调度器和 HTTP 会话并保存到类变量中
        RssPlugin._shared_scheduler = AsyncIOScheduler()
        self.scheduler = RssPlugin._shared_scheduler
        self.scheduler.start()

        RssPlugin._shared_http_session = self.http_session
        # ---------------------------------------

        # WebUI 配置
        self.webui_host = config.get("webui_host", "0.0.0.0")
        self.webui_port = config.get("webui_port", 8888)
        self.webui_token = config.get("webui_token", "")

        # 加载 WebUI 持久化的配置覆盖（重启后不丢失）
        saved_overrides = self.data_handler.data.get("settings", {}).get("config", {})
        for k, v in saved_overrides.items():
            self.config[k] = v

        self._fresh_asyncIOScheduler()

        # 启动 WebUI
        self.webui = RssWebUI(
            self, host=self.webui_host, port=self.webui_port, token=self.webui_token
        )
        start_fut = asyncio.ensure_future(self.webui.start())
        start_fut.add_done_callback(RssPlugin._log_future_error)

    def parse_cron_expr(self, cron_expr: str):
        fields = cron_expr.split() 
        return {
            "minute": fields[0],
            "hour": fields[1],
            "day": fields[2],
            "month": fields[3],
            "day_of_week": fields[4],
        }

    async def parse_channel_info(self, url):
        """获取 RSS XML，支持 ETag/304 节省带宽"""
        try:
            headers = {}
            if url in self._feed_etags:
                headers["If-None-Match"] = self._feed_etags[url]

            async with self.http_session.get(url, headers=headers) as resp:
                if resp.status == 304:
                    return None  # 内容未更新
                if resp.status != 200:
                    self.logger.error(f"rss: 无法正常打开站点 {url}")
                    return None

                etag = resp.headers.get("ETag")
                if etag:
                    self._feed_etags[url] = etag

                return await resp.read()
        except asyncio.TimeoutError:
            self.logger.error(f"rss: 请求站点 {url} 超时")
            return None
        except aiohttp.ClientError as e:
            self.logger.error(f"rss: 请求站点 {url} 网络错误: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"rss: 请求站点 {url} 发生未知错误: {str(e)}")
            return None

    def _entry_fingerprint(self, title: str, link: str, description: str) -> str:
        """生成条目 SHA256 指纹，用于跨轮次去重"""
        material = f"{title}|{link}|{description[:300]}"
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def _cleanup_hashes(self, hashes: list, max_keep: int = 200) -> list:
        """限制哈希列表长度，防止无限膨胀"""
        return hashes[-max_keep:] if len(hashes) > max_keep else hashes

    async def _retry_failed_pushes(self):
        """重试队列中的失败推送，重建完整消息链（含图片），无限重试直到成功"""
        failed = self.data_handler.data.get("_failed_pushes", [])
        if not failed:
            return

        from .rss import RSSItem
        retry_success = []
        for job in failed:
            try:
                item_data = job["item"]
                item = RSSItem(
                    chan_title=item_data["chan_title"],
                    title=item_data["title"],
                    link=item_data["link"],
                    description=item_data["description"],
                    pubDate=item_data["pubDate"],
                    pubDate_timestamp=item_data["pubDate_timestamp"],
                    pic_urls=item_data["pic_urls"],
                )
                comps = await self._get_chain_components(item)
                comps.insert(0, Comp.Plain("🔄 补推:\n"))
                msc = MessageChain(chain=comps, use_t2i_=self.t2i)
                await self.context.send_message(job["user"], msc)
                retry_success.append(job)
                self.logger.info(f"✅ 补推成功: {item_data.get('title', '')[:30]} → {job['user']}")
            except Exception as e:
                self.logger.warning(f"⏳ 补推仍失败 ({job.get('title', '')[:30]}...): {e}，下次继续重试")

        for job in retry_success:
            if job in self.data_handler.data.get("_failed_pushes", []):
                self.data_handler.data["_failed_pushes"].remove(job)

        if retry_success:
            await self.data_handler.save_data()

    async def cron_task_callback(self, url: str, user: str):
        """定时任务回调（含指纹去重 + 失败重试）"""

        if url not in self.data_handler.data:
            return
        if user not in self.data_handler.data[url]["subscribers"]:
            return

        self.logger.info(f"RSS 定时任务触发: {url} - {user}")

        # 先尝试补推之前失败的消息
        await self._retry_failed_pushes()

        last_update = self.data_handler.data[url]["subscribers"][user]["last_update"]
        latest_link = self.data_handler.data[url]["subscribers"][user]["latest_link"]
        pushed_hashes = self.data_handler.data[url]["subscribers"][user].get("pushed_hashes", [])

        rss_items = await self.poll_rss(
            url,
            num=self.max_items_per_poll,
            after_timestamp=last_update,
            after_link=latest_link,
        )
        if not rss_items:
            self.logger.info(f"RSS 定时任务 {url} 无消息更新 - {user}")
            return

        # 指纹去重
        new_items = []
        for item in rss_items:
            fp = self._entry_fingerprint(item.title, item.link, item.description)
            if fp in pushed_hashes:
                continue
            pushed_hashes.append(fp)
            new_items.append(item)

        if not new_items:
            return

        max_ts = last_update
        user_parts = user.split(":")
        platform_name = user_parts[0] if len(user_parts) > 0 else ""

        # 分平台处理消息
        if platform_name == "aiocqhttp" and self.is_compose:
            nodes = []
            for item in new_items:
                comps = await self._get_chain_components(item)
                node = Comp.Node(uin=0, name="Astrbot", content=comps)
                nodes.append(node)
                max_ts = max(max_ts, item.pubDate_timestamp)

            if nodes:
                msc = MessageChain(chain=nodes, use_t2i_=self.t2i)
                try:
                    await self.context.send_message(user, msc)
                except Exception as e:
                    self.logger.error(f"合并推送失败: {e}")
                    # 降级为逐条发送
                    for item in new_items:
                        self._queue_failed_push(user, url, item)
        else:
            for item in new_items:
                comps = await self._get_chain_components(item)
                msc = MessageChain(chain=comps, use_t2i_=self.t2i)
                try:
                    await self.context.send_message(user, msc)
                    max_ts = max(max_ts, item.pubDate_timestamp)
                except Exception as e:
                    self.logger.error(f"推送失败 ({item.title[:30]}...): {e}")
                    self._queue_failed_push(user, url, item)

        # 清理哈希列表，更新状态
        pushed_hashes = self._cleanup_hashes(pushed_hashes)
        self.data_handler.data[url]["subscribers"][user]["pushed_hashes"] = pushed_hashes
        self.data_handler.data[url]["subscribers"][user]["last_update"] = max_ts
        self.data_handler.data[url]["subscribers"][user]["latest_link"] = new_items[-1].link
        await self.data_handler.save_data()
        self.logger.info(f"RSS 定时任务 {url} 推送成功 - {user}")

    def _queue_failed_push(self, user: str, url: str, item):
        """将推送失败的条目加入重试队列（保留完整信息以便补推时重建消息链）"""
        from .rss import RSSItem
        self.data_handler.data.setdefault("_failed_pushes", []).append({
            "user": user,
            "url": url,
            "item": {
                "chan_title": item.chan_title,
                "title": item.title,
                "link": item.link,
                "description": item.description,
                "pubDate": item.pubDate,
                "pubDate_timestamp": item.pubDate_timestamp,
                "pic_urls": item.pic_urls,
            },
            "failed_at": int(time.time()),
        })


    async def poll_rss(
        self,
        url: str,
        num: int = -1,
        after_timestamp: int = 0,
        after_link: str = "",
        raw_text: bytes = None,
    ) -> List[RSSItem]:
        """从站点拉取RSS信息。raw_text 用于复用已获取的 XML，避免重复网络请求。
        使用 feedparser 统一 RSS/Atom/RDF 格式的解析。"""
        if raw_text is None:
            text = await self.parse_channel_info(url)
        else:
            text = raw_text
        if text is None:
            self.logger.error(f"rss: 无法解析站点 {url} 的RSS信息")
            return []

        feed = feedparser.parse(text)
        if feed.bozo:
            self.logger.warning(f"rss: {url} 解析有异常（已尽力恢复）: {feed.bozo_exception}")

        cnt = 0
        rss_items = []

        chan_title = (
            self.data_handler.data[url]["info"]["title"]
            if url in self.data_handler.data
            else feed.feed.get("title", "未知频道")
        )

        for entry in feed.entries:
            try:
                title = entry.get("title", "无标题")
                if len(title) > self.title_max_length:
                    title = title[: self.title_max_length] + "..."

                link = entry.get("link", "")
                if link and not re.match(r"^https?://", link):
                    link = self.data_handler.get_root_url(url) + link

                description = entry.get("summary", "") or entry.get("description", "")
                description, pic_url_list = self.data_handler.parse_html_text_and_pics(description)

                if len(description) > self.description_max_length:
                    description = description[: self.description_max_length] + "..."

                # feedparser：published_parsed 返回 UTC struct_time，用 calendar.timegm 转 timestamp
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_date_timestamp = calendar.timegm(entry.published_parsed)
                    pub_date = entry.get("published", "")
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    pub_date_timestamp = calendar.timegm(entry.updated_parsed)
                    pub_date = entry.get("updated", "")
                else:
                    pub_date_timestamp = 0
                    pub_date = ""

                if pub_date_timestamp > 0:
                    if pub_date_timestamp > after_timestamp:
                        rss_items.append(
                            RSSItem(chan_title, title, link, description,
                                    pub_date, pub_date_timestamp, pic_url_list))
                        cnt += 1
                else:
                    if link != after_link:
                        if not after_link and len(rss_items) >= 1:
                            continue
                        rss_items.append(
                            RSSItem(chan_title, title, link, description,
                                    "", 0, pic_url_list))
                        cnt += 1

                if num != -1 and cnt >= num:
                    break

            except Exception as e:
                self.logger.error(f"rss: 解析Rss条目 {url} 失败: {str(e)}")
                continue

        rss_items.reverse()
        return rss_items

    def parse_rss_url(self, url: str) -> str:
        """解析RSS URL，确保以http或https开头"""
        if not re.match(r"^https?://", url):
            if not url.startswith("/"):
                url = "/" + url
            url = "https://" + url
        return url

    def _add_single_job(self, url: str, user: str, cron_expr: str):
        """增量添加单个定时任务"""
        job_id = f"{url}_{user}"
        self.scheduler.add_job(
            self.cron_task_callback,
            "cron",
            **self.parse_cron_expr(cron_expr),
            args=[url, user],
            id=job_id,
            replace_existing=True,
        )

    def _remove_single_job(self, url: str, user: str):
        """增量移除单个定时任务"""
        job_id = f"{url}_{user}"
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            pass

    def _fresh_asyncIOScheduler(self):
        """刷新定时任务（全量重建，仅在初始化时使用）"""
        self.logger.info("刷新定时任务")
        self.scheduler.remove_all_jobs()

        for url, info in self.data_handler.data.items():
            if url in ["rsshub_endpoints", "settings"]:
                continue
            for user, sub_info in info["subscribers"].items():
                self._add_single_job(url, user, sub_info["cron_expr"])

    async def _add_url(self, url: str, cron_expr: str, message: AstrMessageEvent):
        """内部方法：添加URL订阅的共用逻辑"""
        user = message.unified_msg_origin
        if url in self.data_handler.data:
            latest_item = await self.poll_rss(url)
            last_update = latest_item[-1].pubDate_timestamp if latest_item else int(time.time())
            latest_link = latest_item[-1].link if latest_item else ""
            
            self.data_handler.data[url]["subscribers"][user] = {
                "cron_expr": cron_expr,
                "last_update": last_update,
                "latest_link": latest_link,
            }
        else:
            try:
                text = await self.parse_channel_info(url)
                if text is None:
                    return message.plain_result(f"请求频道失败，请检查网络或 URL: {url}")
                title, desc = self.data_handler.parse_channel_text_info(text)
                latest_item = await self.poll_rss(url, raw_text=text)
            except Exception as e:
                return message.plain_result(f"解析频道信息失败: {str(e)}")

            last_update = latest_item[-1].pubDate_timestamp if latest_item else int(time.time())
            latest_link = latest_item[-1].link if latest_item else ""

            self.data_handler.data[url] = {
                "subscribers": {
                    user: {
                        "cron_expr": cron_expr,
                        "last_update": last_update,
                        "latest_link": latest_link,
                    }
                },
                "info": {
                    "title": title,
                    "description": desc,
                },
            }
        await self.data_handler.save_data()
        return self.data_handler.data[url]["info"]

    def _format_time(self, item: RSSItem) -> str:
        """格式化发布时间为东八区友好的字符串"""
        tz_utc_8 = timezone(timedelta(hours=8))
        formatted_time = ""

        if item.pubDate:
            pub_date_str = str(item.pubDate).strip()
            try:
                dt = email.utils.parsedate_to_datetime(pub_date_str)
                formatted_time = dt.astimezone(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            if not formatted_time:
                try:
                    dt = datetime.strptime(pub_date_str, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(tz_utc_8)
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

        if not formatted_time and item.pubDate_timestamp > 0:
            try:
                dt_corrected = datetime.fromtimestamp(item.pubDate_timestamp, tz=timezone.utc).astimezone(tz_utc_8)
                formatted_time = dt_corrected.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        return formatted_time

    def _build_text_body(self, item: RSSItem) -> str:
        """构建消息文本主体"""
        text_parts = []

        text_parts.append(f"\U0001f4f0 【{item.chan_title}】")
        text_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        formatted_time = self._format_time(item)
        if formatted_time:
            text_parts.append(f"\U0001f552 {formatted_time}")
        elif item.pubDate:
            text_parts.append(f"\U0001f552 {item.pubDate}")

        if not self.is_hide_url and item.link:
            text_parts.append(f"\U0001f517 {item.link}")

        text_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        title = item.title.strip() if item.title else ""
        desc = item.description.strip() if item.description else ""

        if title and title != "无标题" and not desc.startswith(title[:10]):
            text_parts.append(f"\U0001f4cc {title}")
        if desc:
            desc = desc.replace("#", "\n#")
            text_parts.append(f"\U0001f4ac {desc}")

        return "\n".join(text_parts)

    async def _fetch_images(self, item: RSSItem) -> list:
        """并行下载 RSS 条目的图片，返回 Image 组件列表"""
        comps = []
        if self.is_read_pic and item.pic_urls:
            temp_max_pic_item = len(item.pic_urls) if self.max_pic_item == -1 else self.max_pic_item
            pic_urls = item.pic_urls[:temp_max_pic_item]
            results = await asyncio.gather(
                *(self.pic_handler.modify_corner_pixel_to_base64(url) for url in pic_urls),
                return_exceptions=True,
            )
            for base64str in results:
                if base64str is None or isinstance(base64str, Exception):
                    comps.append(Comp.Plain("\n[图片加载失败]"))
                else:
                    comps.append(Comp.Plain("\n"))
                    comps.append(Comp.Image.fromBase64(base64str))
        return comps

    async def _get_chain_components(self, item: RSSItem):
        """组装消息链"""
        comps = []
        text = self._build_text_body(item)
        comps.append(Comp.Plain(text))

        image_comps = await self._fetch_images(item)
        comps.extend(image_comps)

        return comps
    
    def _is_url_or_ip(self,text: str) -> bool:
        """
        判断一个字符串是否为网址（http/https 开头）或 IP 地址。
        """
        url_pattern = r"^(?:http|https)://.+$"
        ip_pattern = r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(:\d+)?$"
        return bool(re.match(url_pattern, text) or re.match(ip_pattern, text))

    @staticmethod
    def _log_future_error(fut):
        """记录 fire-and-forget 任务中的异常"""
        try:
            exc = fut.exception()
            if exc:
                logger = logging.getLogger("astrbot")
                logger.error(f"后台任务失败: {exc}")
        except asyncio.InvalidStateError:
            pass

    async def close(self):
        """清理插件资源：停止 WebUI、关闭调度器、关闭 HTTP 会话"""
        if hasattr(self, 'webui') and hasattr(self.webui, 'stop'):
            try:
                await self.webui.stop()
            except Exception as e:
                self.logger.warning(f"关闭 WebUI 时出错: {e}")

        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

        if not self.http_session.closed:
            await self.http_session.close()

        if RssPlugin._shared_scheduler is self.scheduler:
            RssPlugin._shared_scheduler = None
        if RssPlugin._shared_http_session is self.http_session:
            RssPlugin._shared_http_session = None

    @filter.command_group("rss", alias={"RSS"})
    def rss(self):
        pass

    @rss.group("rsshub")
    def rsshub(self, event: AstrMessageEvent):
        pass

    @rsshub.command("add")
    async def rsshub_add(self, event: AstrMessageEvent, url: str):
        if url.endswith("/"):
            url = url[:-1]
        url = self.parse_rss_url(url)
        
        if not self._is_url_or_ip(url):
            yield event.plain_result("请输入正确的URL")
            return
        elif url in self.data_handler.data["rsshub_endpoints"]:
            yield event.plain_result("该RSSHub端点已存在")
            return
        else:
            self.data_handler.data["rsshub_endpoints"].append(url)
            await self.data_handler.save_data()
            yield event.plain_result("添加成功")

    @rsshub.command("list")
    async def rsshub_list(self, event: AstrMessageEvent):
        ret = "当前Bot添加的rsshub endpoint：\n"
        yield event.plain_result(
            ret
            + "\n".join(
                [
                    f"{i}: {x}"
                    for i, x in enumerate(self.data_handler.data["rsshub_endpoints"])
                ]
            )
        )

    @rsshub.command("remove")
    async def rsshub_remove(self, event: AstrMessageEvent, idx: int):
        if idx < 0 or idx >= len(self.data_handler.data["rsshub_endpoints"]):
            yield event.plain_result("索引越界")
            return
        else:
            self.data_handler.data["rsshub_endpoints"].pop(idx)
            await self.data_handler.save_data()
            self._fresh_asyncIOScheduler()
            yield event.plain_result("删除成功")

    @rss.command("add")
    async def add_command(
        self,
        event: AstrMessageEvent,
        idx: int,
        route: str,
        minute: str,
        hour: str,
        day: str,
        month: str,
        day_of_week: str,
    ):
        if idx < 0 or idx >= len(self.data_handler.data["rsshub_endpoints"]):
            yield event.plain_result(
                "索引越界, 请使用 /rss rsshub list 查看已经添加的 rsshub endpoint"
            )
            return
        if not route.startswith("/"):
            yield event.plain_result("路由必须以 / 开头")
            return

        url = self.data_handler.data["rsshub_endpoints"][idx] + route
        cron_expr = f"{minute} {hour} {day} {month} {day_of_week}"

        ret = await self._add_url(url, cron_expr, event)
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
        self,
        event: AstrMessageEvent,
        url: str,
        minute: str,
        hour: str,
        day: str,
        month: str,
        day_of_week: str,
    ):
        url = self.parse_rss_url(url)
        cron_expr = f"{minute} {hour} {day} {month} {day_of_week}"
        ret = await self._add_url(url, cron_expr, event)
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
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if not subs_urls:
            yield event.plain_result("当前没有任何订阅。")
            return
        ret = "📋 当前订阅列表：\n"
        for i, url in enumerate(subs_urls):
            info = self.data_handler.data[url]["info"]
            cron = self.data_handler.data[url]["subscribers"][user].get("cron_expr", "* * * * *")
            ret += f"{i}. 【{info['title']}】\n   周期: `{cron}`\n"
        yield event.plain_result(ret.strip())

    @rss.command("remove")
    async def remove_command(self, event: AstrMessageEvent, idx: int):
        subs_urls = self.data_handler.get_subs_channel_url(event.unified_msg_origin)
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

    @rss.command("update")
    async def update_command(self, event: AstrMessageEvent, idx: int, minute: str, hour: str, day: str, month: str, day_of_week: str):
        user = event.unified_msg_origin
        subs_urls = self.data_handler.get_subs_channel_url(user)
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引错误。")
            return
        url = subs_urls[idx]
        new_cron = f"{minute} {hour} {day} {month} {day_of_week}"
        self.data_handler.data[url]["subscribers"][user]["cron_expr"] = new_cron
        await self.data_handler.save_data()
        self._remove_single_job(url, user)
        self._add_single_job(url, user, new_cron)
        yield event.plain_result(f"✅ 更新成功！\n频道: {self.data_handler.data[url]['info']['title']}\n新周期: `{new_cron}`")


    @rss.command("get")
    async def get_command(self, event: AstrMessageEvent, idx: int):
        subs_urls = self.data_handler.get_subs_channel_url(event.unified_msg_origin)
        if idx < 0 or idx >= len(subs_urls):
            yield event.plain_result("索引越界, 请使用 /rss list 查看已经添加的订阅")
            return
        url = subs_urls[idx]
        rss_items = await self.poll_rss(url)
        if not rss_items:
            yield event.plain_result("没有新的订阅内容")
            return
        
        item = rss_items[-1]
        user_parts = event.unified_msg_origin.split(":")
        platform_name = user_parts[0] if len(user_parts) > 0 else ""

        comps = await self._get_chain_components(item)
        if(platform_name == "aiocqhttp" and self.is_compose):
            node = Comp.Node(
                    uin=0,
                    name="Astrbot",
                    content=comps
                )
            yield event.chain_result([node]).use_t2i(self.t2i)
        else:
            yield event.chain_result(comps).use_t2i(self.t2i)