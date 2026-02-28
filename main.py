import aiohttp
import asyncio
import time
import re
import logging
from lxml import etree
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult,MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
import astrbot.api.message_components as Comp

from .data_handler import DataHandler
from .pic_handler import RssImageHandler
from .rss import RSSItem
from typing import List


@register(
    "astrbot_plugin_rss",
    "Xiaobailoves",
    "RSS订阅插件",
    "1.2.0",
    "https://github.com/xiaobailoves/astrbot_plugin_rss",
)
class RssPlugin(Star):
    # 增加一个类变量，用于存储调度器实例，防止热重载时产生多个定时器泄漏（幽灵任务）
    _shared_scheduler = None

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)

        self.logger = logging.getLogger("astrbot")
        self.context = context
        self.config = config
        self.data_handler = DataHandler()

        # 提取scheme文件中的配置
        self.title_max_length = config.get("title_max_length")
        self.description_max_length = config.get("description_max_length")
        self.max_items_per_poll = config.get("max_items_per_poll")
        self.t2i = config.get("t2i")
        self.is_hide_url = config.get("is_hide_url")
        self.is_read_pic= config.get("pic_config").get("is_read_pic")
        self.is_adjust_pic= config.get("pic_config").get("is_adjust_pic")
        self.max_pic_item = config.get("pic_config").get("max_pic_item")
        self.is_compose = config.get("compose")
        self.proxy = config.get("proxy", None) # 新增代理配置

        # 传入代理配置给图片处理器，这里保留代理
        self.pic_handler = RssImageHandler(self.is_adjust_pic, proxy=self.proxy)
        
        # --- 修复定时任务重复触发（泄漏）的 Bug ---
        if RssPlugin._shared_scheduler is not None and RssPlugin._shared_scheduler.running:
            try:
                RssPlugin._shared_scheduler.shutdown(wait=False)
            except Exception as e:
                self.logger.warning(f"关闭旧调度器时出现异常: {e}")
        
        # 创建新的调度器并保存到类变量中
        RssPlugin._shared_scheduler = AsyncIOScheduler()
        self.scheduler = RssPlugin._shared_scheduler
        self.scheduler.start()
        # ---------------------------------------

        self._fresh_asyncIOScheduler()

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
        headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        connector = aiohttp.TCPConnector(ssl=False)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        try:
            # 【修改点 1】：将 trust_env 改为 False，确保拉取 RSS 时不走系统级代理
            async with aiohttp.ClientSession(trust_env=False,
                                        connector=connector,
                                        timeout=timeout,
                                        headers=headers
                                        ) as session:
                # 【修改点 2】：移除 proxy=self.proxy，确保拉取 RSS 纯直连
                async with session.get(url) as resp: 
                    if resp.status != 200:
                        self.logger.error(f"rss: 无法正常打开站点 {url}")
                        return None
                    text = await resp.read()
                    return text
        except asyncio.TimeoutError:
            self.logger.error(f"rss: 请求站点 {url} 超时")
            return None
        except aiohttp.ClientError as e:
            self.logger.error(f"rss: 请求站点 {url} 网络错误: {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"rss: 请求站点 {url} 发生未知错误: {str(e)}")
            return None

    async def cron_task_callback(self, url: str, user: str):
        """定时任务回调"""

        if url not in self.data_handler.data:
            return
        if user not in self.data_handler.data[url]["subscribers"]:
            return

        self.logger.info(f"RSS 定时任务触发: {url} - {user}")
        last_update = self.data_handler.data[url]["subscribers"][user]["last_update"]
        latest_link = self.data_handler.data[url]["subscribers"][user]["latest_link"]
        max_items_per_poll = self.max_items_per_poll
        # 拉取 RSS
        rss_items = await self.poll_rss(
            url,
            num=max_items_per_poll,
            after_timestamp=last_update,
            after_link=latest_link,
        )
        if not rss_items:
            self.logger.info(f"RSS 定时任务 {url} 无消息更新 - {user}")
            return
            
        max_ts = last_update

        user_parts = user.split(":")
        platform_name = user_parts[0] if len(user_parts) > 0 else ""
        message_type = user_parts[1] if len(user_parts) > 1 else ""
        session_id = user_parts[2] if len(user_parts) > 2 else ""

        # 分平台处理消息
        if platform_name == "aiocqhttp" and self.is_compose:
            nodes = []
            for item in rss_items:
                comps = await self._get_chain_components(item)
                node = Comp.Node(
                            uin=0,
                            name="Astrbot",
                            content=comps
                        )
                nodes.append(node)
                max_ts = max(max_ts, item.pubDate_timestamp)

            # 合并消息发送
            if len(nodes) > 0:
                msc = MessageChain(
                    chain=nodes,
                    use_t2i_= self.t2i
                )
                await self.context.send_message(user, msc)
        else:
            # 每个消息单独发送
            for item in rss_items:
                comps = await self._get_chain_components(item)
                msc = MessageChain(
                    chain=comps,
                    use_t2i_= self.t2i
                )
                await self.context.send_message(user, msc)
                max_ts = max(max_ts, item.pubDate_timestamp)

        # 更新最后更新时间
        self.data_handler.data[url]["subscribers"][user]["last_update"] = max_ts
        self.data_handler.data[url]["subscribers"][user]["latest_link"] = rss_items[-1].link
        self.data_handler.save_data()
        self.logger.info(f"RSS 定时任务 {url} 推送成功 - {user}")


    async def poll_rss(
        self,
        url: str,
        num: int = -1,
        after_timestamp: int = 0,
        after_link: str = "",
    ) -> List[RSSItem]:
        """从站点拉取RSS信息"""
        text = await self.parse_channel_info(url)
        if text is None:
            self.logger.error(f"rss: 无法解析站点 {url} 的RSS信息")
            return []
        root = etree.fromstring(text)
        items = root.xpath("//item")

        cnt = 0
        rss_items = []

        for item in items:
            try:
                chan_title = (
                    self.data_handler.data[url]["info"]["title"]
                    if url in self.data_handler.data
                    else "未知频道"
                )

                raw_title = item.xpath("title")
                title = raw_title[0].text if (raw_title and raw_title[0].text) else "无标题"
                
                if len(title) > self.title_max_length:
                    title = title[: self.title_max_length] + "..."

                link_nodes = item.xpath("link")
                link = link_nodes[0].text if (link_nodes and link_nodes[0].text) else ""
                if link and not re.match(r"^https?://", link):
                    link = self.data_handler.get_root_url(url) + link
                
                raw_desc = item.xpath("description")
                description = raw_desc[0].text if (raw_desc and raw_desc[0].text) else ""

                pic_url_list = self.data_handler.strip_html_pic(description)
                description = self.data_handler.strip_html(description)

                if len(description) > self.description_max_length:
                    description = (
                        description[: self.description_max_length] + "..."
                    )

                pub_date_nodes = item.xpath("pubDate")
                if pub_date_nodes:
                    pub_date = item.xpath("pubDate")[0].text
                    try:
                        pub_date_parsed = time.strptime(
                            pub_date.replace("GMT", "+0000"),
                            "%a, %d %b %Y %H:%M:%S %z",
                        )
                        pub_date_timestamp = int(time.mktime(pub_date_parsed))
                    except:
                        pub_date_timestamp = 0

                    if pub_date_timestamp > after_timestamp:
                        rss_items.append(
                            RSSItem(
                                chan_title,
                                title,
                                link,
                                description,
                                pub_date,
                                pub_date_timestamp,
                                pic_url_list
                            )
                        )
                        cnt += 1
                else:
                    if link != after_link:
                        if not after_link and len(rss_items) >= 1:
                            continue
                        rss_items.append(
                            RSSItem(chan_title, title, link, description, "", 0, pic_url_list)
                        )
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

    def _fresh_asyncIOScheduler(self):
        """刷新定时任务"""
        self.logger.info("刷新定时任务")
        self.scheduler.remove_all_jobs()

        for url, info in self.data_handler.data.items():
            if url in ["rsshub_endpoints", "settings"]:
                continue
            for user, sub_info in info["subscribers"].items():
                job_id = f"{url}_{user}" 
                self.scheduler.add_job(
                    self.cron_task_callback,
                    "cron",
                    **self.parse_cron_expr(sub_info["cron_expr"]),
                    args=[url, user],
                    id=job_id,
                    replace_existing=True 
                )

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
                latest_item = await self.poll_rss(url)
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
        self.data_handler.save_data()
        return self.data_handler.data[url]["info"]

    async def _get_chain_components(self, item: RSSItem):
        """组装消息链（已修复时间与排版）"""
        from datetime import datetime, timezone, timedelta
        
        comps = []
        text_parts = [] # 使用列表收集所有文本，最后一次性拼接，解决换行丢失问题
        
        # 1. 醒目的头部：频道名称
        is_rt = title.upper().startswith("RT") or desc.upper().startswith("RT")
        source_name = item.chan_title.replace("Twitter @", "🐦 ").strip()
        text_parts.append(f"📰 【{item.chan_title}】")
        text_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")# 替换为较轻量的分割线
        
        # 4. 【核心修复】友好的发布时间 (强制使用东八区 UTC+8)
        # 设定一个固定的东八区时区，绕开服务器系统的本地时区设置
        tz_utc_8 = timezone(timedelta(hours=8))
        formatted_time = ""
        
        # 优先读取原始字符串，无视被上游解析错误的时间戳
        if getattr(item, 'pub_date', None):
            pub_date_str = str(item.pub_date).strip()
            
            try:
                # 尝试 A: 如果它是完整的 RSSHub 格式 (如 Sat, 28 Feb 2026 06:37:12 GMT)
                dt = email.utils.parsedate_to_datetime(pub_date_str)
                formatted_time = dt.astimezone(tz_utc_8).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
                
            if not formatted_time:
                try:
                    # 尝试 B: 上游瞎眼把它截断成了纯粹的 "2026-02-28 06:37:12"
                    # 根据你的反馈，RSSHub源是GMT，所以我们将错就错，强行给这个字符串贴上 UTC 标签并 +8 小时
                    dt = datetime.strptime(pub_date_str, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(tz_utc_8)
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
                    
        # 尝试 C: 如果全失败了，才使用带有污染的 timestamp 进行暴力修正兜底
        if not formatted_time and getattr(item, 'pubDate_timestamp', 0) > 0:
            try:
                # 获取它错误的本地字面时间，将其强行视为 UTC 时间，然后转为东八区
                dt_naive = datetime.fromtimestamp(item.pubDate_timestamp)
                dt_corrected = dt_naive.replace(tzinfo=timezone.utc).astimezone(tz_utc_8)
                formatted_time = dt_corrected.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        # 兜底输出时间
        if formatted_time:
            text_parts.append(f"🕒 {formatted_time}")
        elif getattr(item, 'pub_date', None):
            text_parts.append(f"🕒 {item.pub_date}")

        # 5. 来源链接 (放置底部防止排版被破坏)
        if not getattr(self, 'is_hide_url', False) and getattr(item, 'link', None):
            text_parts.append(f"🔗 {item.link}")

        text_parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        title = item.title.strip() if item.title else ""
        desc = item.description.strip() if item.description else ""

        display_desc = desc
        if is_rt:
            # 将 "RT " 替换为图标，并尝试截断过于冗长的博主后缀（以空格或特定符号分割）
            display_desc = display_desc.replace("RT ", "🔄 转发自: ").replace("RT ", "🔄 转发自: ")

        show_title = True
        if title and desc.startswith(title[:15]):
            show_title = False
        # 3. 标题与正文逻辑处理
        # 如果标题存在且不是“无标题”
        if show_title and title != "无标题" and not desc.startswith(title[:10]):
            text_parts.append(f"📌 {title}")
        if display_desc:
            # 给话题标签前后增加空格，或者单独换行（可选）
            display_desc = display_desc.replace("#", "\n#") 
            text_parts.append(f"💬 {display_desc}")

        # === 合并所有文本组件 ===
        # 将上面收集的所有文本用换行符(\n)连接成一个完整的字符串
        # 彻底避免多个 Comp.Plain 拼接时导致的排版挤压和换行失效问题
        full_text = "\n".join(text_parts)
        comps.append(Comp.Plain(full_text))

        # 7. 图片处理 (保留原逻辑)
        if self.is_read_pic and item.pic_urls:
            temp_max_pic_item = len(item.pic_urls) if self.max_pic_item == -1 else self.max_pic_item
            for pic_url in item.pic_urls[:temp_max_pic_item]:
                base64str = await self.pic_handler.modify_corner_pixel_to_base64(pic_url)
                if base64str is None:
                    comps.append(Comp.Plain("\n[图片加载失败]"))
                    continue
                else:
                    comps.append(Comp.Plain("\n")) # 换行防粘连
                    comps.append(Comp.Image.fromBase64(base64str))
                    
        return comps
    
    def _is_url_or_ip(self,text: str) -> bool:
        """
        判断一个字符串是否为网址（http/https 开头）或 IP 地址。
        """
        url_pattern = r"^(?:http|https)://.+$"
        ip_pattern = r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(:\d+)?$"
        return bool(re.match(url_pattern, text) or re.match(ip_pattern, text))

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
            self.data_handler.save_data()
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
            self.data_handler.save_data()
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

        self._fresh_asyncIOScheduler()

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

        self._fresh_asyncIOScheduler()

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
        self.data_handler.data[url]["subscribers"].pop(event.unified_msg_origin)

        if not self.data_handler.data[url]["subscribers"]:
            self.data_handler.data.pop(url)

        self.data_handler.save_data()

        self._fresh_asyncIOScheduler()
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
        self.data_handler.save_data()
        self._fresh_asyncIOScheduler()
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