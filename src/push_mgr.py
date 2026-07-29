"""推送执行模块 —— 定时回调、失败重试、历史记录"""
import logging
import time

from astrbot.api.event import MessageChain
import astrbot.api.message_components as Comp

from .data_handler import DataHandler
from .feed_fetcher import FeedFetcher
from .msg_builder import MessageBuilder
from .models import get_renderer, BaseRenderer
from .rss import RSSItem


class PushManager:
    """负责定时推送执行、失败重试队列和推送历史记录"""

    UNRECOVERABLE_ERROR_PATTERNS = (
        "no target session", "target session is empty", "invalid session",
        "session not found", "user banned", "permission denied", "forbidden",
        "not found", "invalid target",
    )

    def __init__(
        self,
        context,                 # AstrBot Context（用于 send_message）
        data_handler: DataHandler,
        fetcher: FeedFetcher,
        builder: MessageBuilder,
        renderers: dict[str, BaseRenderer],
        is_compose: bool = True,
        t2i: bool = False,
        max_items_per_poll: int = 3,
        max_consecutive_failures: int = 100,
    ):
        self._context = context
        self._data_handler = data_handler
        self._fetcher = fetcher
        self._builder = builder
        self._renderers = renderers
        self._is_compose = is_compose
        self._t2i = t2i
        self._max_items_per_poll = max_items_per_poll
        self._max_failures = max_consecutive_failures
        self._logger = logging.getLogger("astrbot")

    # ── Helpers ───────────────────────────────────────────

    async def _apply_filter(self, sub: dict, items: list) -> list:
        """内容过滤：off / regex / ai"""
        mode = sub.get("filter_mode", "off")
        if mode == "off" or not items:
            return items

        if mode == "regex":
            blacklist = sub.get("filter_blacklist", [])
            whitelist = sub.get("filter_whitelist", [])
            if not blacklist and not whitelist:
                return items
            import re
            passed = []
            for item in items:
                text = f"{item.title} {item.description}"
                # 白名单检查
                if whitelist:
                    if not any(self._match_kw(text, kw) for kw in whitelist):
                        continue
                # 黑名单检查
                if blacklist:
                    if any(self._match_kw(text, kw) for kw in blacklist):
                        continue
                passed.append(item)
            self._logger.debug(
                f"🔇 正则过滤: {len(items)}→{len(passed)} 条"
            )
            return passed

        if mode == "ai":
            try:
                provider = self._context.get_using_provider()
            except Exception:
                self._logger.warning("AI 过滤不可用：无法获取 LLM provider")
                return items
            prompt_tpl = sub.get("filter_ai_prompt",
                '判断以下 RSS 条目是否值得推送，只回复"是"或"否"。\n\n标题: {title}\n正文: {body}')
            passed = []
            for item in items:
                prompt = prompt_tpl.format(
                    title=item.title, body=(item.description or "")[:500]
                )
                try:
                    resp = await provider.text_chat(prompt)
                    if resp and "是" in str(resp)[:3]:
                        passed.append(item)
                except Exception as e:
                    self._logger.warning(f"AI 过滤调用失败: {e}，保留条目")
                    passed.append(item)
            self._logger.debug(
                f"🤖 AI 过滤: {len(items)}→{len(passed)} 条"
            )
            return passed

        return items  # unknown mode → pass through

    @staticmethod
    def _match_kw(text: str, kw: str) -> bool:
        """匹配关键词：regex: 开头为正则，否则为大小写不敏感包含"""
        if kw.startswith("regex:"):
            import re
            return bool(re.search(kw[6:], text))
        return kw.lower() in text.lower()

    def _on_failure(self, data: dict, url: str, user: str, reason: str = ""):
        """跟踪连续失败次数，超过阈值自动暂停"""
        sub = data[url]["subscribers"][user]
        count = sub.get("consecutive_failures", 0) + 1
        sub["consecutive_failures"] = count
        if count >= self._max_failures:
            sub["paused"] = True
            self._logger.warning(
                f"🚫 RSS {url} - {user} 连续失败 {count} 次（原因: {reason}），已自动暂停"
            )
        else:
            self._logger.debug(
                f"⚠️ RSS {url} - {user} 连续失败 {count}/{self._max_failures} 次（原因: {reason}）"
            )

    def _is_unrecoverable(self, error: Exception) -> bool:
        """判断是否为不可恢复的推送错误"""
        err_msg = str(error).lower()
        return any(p in err_msg for p in self.UNRECOVERABLE_ERROR_PATTERNS)

    def _record_history(self, url: str, user: str, item: RSSItem):
        """记录推送历史，保留最近 200 条"""
        self._data_handler.data.setdefault("_push_history", []).append({
            "time": int(time.time()),
            "chan_title": item.chan_title,
            "title": item.title,
            "url": url,
            "user": user,
        })
        h = self._data_handler.data["_push_history"]
        if len(h) > 200:
            self._data_handler.data["_push_history"] = h[-200:]

    def _queue_failed(self, user: str, url: str, item: RSSItem):
        """将推送失败的条目加入重试队列"""
        self._data_handler.data.setdefault("_failed_pushes", []).append({
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
                "guid": getattr(item, 'guid', ''),
            },
            "failed_at": int(time.time()),
        })

    def _pick_renderer(self, user: str, url: str) -> BaseRenderer:
        """为指定订阅选择合适的渲染器"""
        sub = self._data_handler.data[url]["subscribers"][user]
        renderer_name = sub.get("renderer")
        platform = user.split(":")[0] if ":" in user else ""
        return get_renderer(
            self._renderers, renderer_name, platform, self._is_compose
        )

    # ── Retry ─────────────────────────────────────────────

    async def retry_failed(self):
        """重试队列中的失败推送"""
        failed = self._data_handler.data.get("_failed_pushes", [])
        if not failed:
            return

        retry_success = []
        retry_giveup = []
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
                    guid=item_data.get("guid", ""),
                )
                # 补推使用默认渲染器排版，前缀标记
                renderer = self._renderers.get("default")
                if renderer is None:
                    renderer = next(iter(self._renderers.values()))
                comps = await renderer.render_item(item)
                comps.insert(0, Comp.Plain("🔄 补推:\n"))
                msc = MessageChain(chain=comps, use_t2i_=self._t2i)
                await self._context.send_message(job["user"], msc)
                retry_success.append(job)
                self._record_history(job["url"], job["user"], item)
                self._logger.info(
                    f"✅ 补推成功: {item_data.get('title', '')[:30]} → {job['user']}"
                )
            except Exception as e:
                if self._is_unrecoverable(e):
                    self._logger.warning(
                        f"❌ 补推遇到不可恢复错误 ({item_data.get('title', '')[:30]}...): {e}，放弃重试"
                    )
                    retry_giveup.append(job)
                else:
                    self._logger.warning(
                        f"⏳ 补推仍失败 ({item_data.get('title', '')[:30]}...): {e}，下次继续重试"
                    )

        for job in retry_success + retry_giveup:
            if job in self._data_handler.data.get("_failed_pushes", []):
                self._data_handler.data["_failed_pushes"].remove(job)

        if retry_success or retry_giveup:
            await self._data_handler.save_data()

    # ── Cron callback ─────────────────────────────────────

    async def execute(self, url: str, user: str):
        """定时任务回调（含指纹去重 + 失败重试）"""
        data = self._data_handler.data

        if url not in data:
            return
        if user not in data[url]["subscribers"]:
            return

        sub = data[url]["subscribers"][user]
        if sub.get("paused"):
            return  # 已暂停，静默跳过

        async with self._fetcher.get_lock(url):
            self._logger.info(f"⏰ RSS 定时任务触发: {url} - {user}")

            await self.retry_failed()

            last_update = data[url]["subscribers"][user]["last_update"]
            latest_link = data[url]["subscribers"][user]["latest_link"]
            pushed_hashes = list(data[url]["subscribers"][user].get("pushed_hashes", []))
            poll_start_ts = int(time.time())
            update_mode = sub.get("update_mode", "time")

            if update_mode == "guid":
                # GUID 模式：跳过时间过滤，纯哈希去重
                rss_items = await self._fetcher.poll(
                    url, num=self._max_items_per_poll,
                    after_timestamp=0, after_link="",
                )
            else:
                # 时间模式：现有逻辑
                rss_items = await self._fetcher.poll(
                    url,
                    num=self._max_items_per_poll,
                    after_timestamp=last_update,
                    after_link=latest_link,
                )
            if not rss_items:
                self._logger.info(f"😴 RSS {url} 无新内容 - {user}")
                self._on_failure(data, url, user, "无新内容")
                return

            # 多指纹去重：任一匹配即跳过
            new_items = []
            for item in rss_items:
                fps = self._fetcher.fingerprints(
                    getattr(item, 'guid', ''), item.link, item.title, item.description
                )
                if any(fp in pushed_hashes for fp in fps):
                    continue
                pushed_hashes.extend(fps)
                new_items.append(item)

            if not new_items:
                return

            # ── 内容过滤 ────────────────────────────────
            new_items = await self._apply_filter(sub, new_items)

            if not new_items:
                self._logger.debug(f"🔇 RSS {url} 过滤后无条目 - {user}")
                return

            # ── 渲染器调度 ──────────────────────────────
            renderer = self._pick_renderer(user, url)
            max_ts = last_update
            try:
                max_ts = await renderer.send(
                    self._context, user, new_items, self._t2i
                )
                for item in new_items:
                    self._record_history(url, user, item)
                self._logger.debug(
                    f"📄 使用渲染器: {renderer.name} ({url})"
                )

                # 成功：重置失败计数 + 保存状态
                # 重检查：推送期间订阅可能已被删除
                if url not in data or user not in data[url].get("subscribers", {}):
                    self._logger.debug(f"⚠️ RSS {url} 推送期间订阅已被删除 - {user}")
                    return
                data[url]["subscribers"][user]["consecutive_failures"] = 0
                pushed_hashes = self._fetcher.cleanup_hashes(pushed_hashes)
                data[url]["subscribers"][user]["pushed_hashes"] = pushed_hashes
                data[url]["subscribers"][user]["last_update"] = poll_start_ts
                data[url]["subscribers"][user]["latest_link"] = new_items[-1].link
                await self._data_handler.save_data()
                self._logger.info(f"✅ RSS {url} 推送完成 - {user}")

            except Exception as e:
                self._on_failure(data, url, user, str(e)[:80])
                if self._is_unrecoverable(e):
                    self._logger.warning(f"🚫 不可恢复的推送错误，跳过重试: {e}")
                else:
                    self._logger.error(f"❌ 推送失败: {e}")
                    for item in new_items:
                        self._queue_failed(user, url, item)
