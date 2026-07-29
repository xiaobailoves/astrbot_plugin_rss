"""RSS 插件 Plugin Pages API —— 通过 AstrBot context.register_web_api 注册路由"""
import logging

from quart import request, jsonify

_logger = logging.getLogger("astrbot.rss")


class RssWebApi:
    """将原 aiohttp WebUI 的 API 端点迁移到 AstrBot Plugin Pages 框架"""

    def __init__(self, plugin):
        self._plugin = plugin

    def register(self, context):
        prefix = "/astrbot_plugin_rss"

        routes = [
            ("GET",  "/subscriptions",      self._list_subs,        "列出用户订阅"),
            ("GET",  "/subscriptions/all",  self._list_all_subs,     "列出全部订阅"),
            ("POST", "/subscriptions/rsshub", self._add_sub_rsshub,  "通过 RSSHub 添加"),
            ("POST", "/subscriptions/url",  self._add_sub_url,       "通过 URL 添加"),
            ("POST", "/subscriptions/fetch", self._fetch_items,      "手动拉取条目"),
            ("POST", "/subscriptions/update", self._update_sub,      "更新订阅"),
            ("POST", "/subscriptions/delete", self._del_sub,         "删除订阅"),
            ("POST", "/subscriptions/pause", self._pause_sub,        "暂停订阅"),
            ("POST", "/subscriptions/resume", self._resume_sub,      "恢复订阅"),
            ("GET",  "/stats",              self._stats,             "插件统计"),
            ("GET",  "/rsshub",             self._list_endpoints,    "列出 RSSHub 端点"),
            ("POST", "/rsshub",             self._add_endpoint,      "添加 RSSHub 端点"),
            ("POST", "/rsshub/delete",      self._del_endpoint,      "删除 RSSHub 端点"),
            ("GET",  "/config",             self._get_config,        "获取配置"),
            ("POST", "/config",             self._update_config,     "更新配置"),
            ("POST", "/reload",             self._reload_scheduler,  "重载调度器"),
            ("GET",  "/history",            self._list_history,      "推送历史"),
            ("GET",  "/logs",               self._list_logs,         "插件日志"),
        ]

        for method, endpoint, handler, desc in routes:
            context.register_web_api(f"{prefix}{endpoint}", handler, [method], desc)
        _logger.info(f"RSS Web API 已注册 {len(routes)} 个端点")

    # ── helpers ───────────────────────────────────────────

    def _get_user_subs(self, user):
        dh = self._plugin.data_handler
        result = []
        for url, info in dh.data.items():
            if url in ("rsshub_endpoints", "settings") or url.startswith("_"):
                continue
            if user in info.get("subscribers", {}):
                sub = info["subscribers"][user]
                result.append({
                    "url": url,
                    "title": info.get("info", {}).get("title", "未知频道"),
                    "desc": info.get("info", {}).get("description", ""),
                    "cron": sub.get("cron_expr", ""),
                    "renderer": sub.get("renderer", ""),
                    "paused": sub.get("paused", False),
                    "last_update": sub.get("last_update", 0),
                })
        return result

    # ── Subscriptions ─────────────────────────────────────

    async def _list_subs(self):
        user = request.args.get("user", "")
        if not user:
            return jsonify({"error": "缺少 user 参数"}), 400
        return jsonify(self._get_user_subs(user))

    async def _list_all_subs(self):
        dh = self._plugin.data_handler
        result = []
        for url, info in dh.data.items():
            if url in ("rsshub_endpoints", "settings") or url.startswith("_"):
                continue
            for user, sub in info.get("subscribers", {}).items():
                result.append({
                    "user": user, "url": url,
                    "title": info.get("info", {}).get("title", "unknown"),
                    "cron": sub.get("cron_expr", ""),
                    "paused": sub.get("paused", False),
                    "renderer": sub.get("renderer", ""),
                    "filter_mode": sub.get("filter_mode", "off"),
                    "update_mode": sub.get("update_mode", "time"),
                    "filter_blacklist": sub.get("filter_blacklist", []),
                    "filter_whitelist": sub.get("filter_whitelist", []),
                })
        return jsonify(result)

    async def _add_sub_rsshub(self):
        data = await request.get_json()
        user = data.get("user", "")
        ep_idx = data.get("endpoint_idx", -1)
        route = data.get("route", "")
        cron = data.get("cron", "0 * * * *")
        if not user or not route:
            return jsonify({"error": "user / route 不能为空"}), 400
        eps = self._plugin.data_handler.data.get("rsshub_endpoints", [])
        if ep_idx < 0 or ep_idx >= len(eps):
            return jsonify({"error": "端点索引无效"}), 400
        url = eps[ep_idx] + route
        try:
            ok, info = await self._plugin._add_subscription(url, cron, user)
            if not ok:
                return jsonify({"error": info}), 400
        except Exception as e:
            return jsonify({"error": f"添加失败: {e}"}), 500
        self._plugin._add_single_job(url, user, cron)
        return jsonify({"ok": True, "url": url, "title": info["title"]})

    async def _add_sub_url(self):
        data = await request.get_json()
        user = data.get("user", "")
        url = data.get("url", "")
        cron = data.get("cron", "0 * * * *")
        renderer = data.get("renderer")
        if not user or not url:
            return jsonify({"error": "user / url 不能为空"}), 400
        url = self._plugin.fetcher.normalize_url(url)
        try:
            ok, info = await self._plugin._add_subscription(url, cron, user, renderer)
            if not ok:
                return jsonify({"error": info}), 400
        except Exception as e:
            return jsonify({"error": f"添加失败: {e}"}), 500
        self._plugin._add_single_job(url, user, cron)
        return jsonify({"ok": True, "url": url, "title": info["title"]})

    async def _del_sub(self):
        data = await request.get_json()
        user = data.get("user", "")
        url = data.get("url", "")
        if not user:
            return jsonify({"error": "缺少 user 参数"}), 400
        if not url:
            idx = data.get("idx", -1)
            subs_urls = self._plugin.data_handler.get_subs_channel_url(user)
            if idx < 0 or idx >= len(subs_urls):
                return jsonify({"error": "索引越界"}), 404
            url = subs_urls[idx]
        self._plugin.data_handler.data[url]["subscribers"].pop(user, None)
        if not self._plugin.data_handler.data[url]["subscribers"]:
            self._plugin.data_handler.data.pop(url)
        self._plugin._remove_single_job(url, user)
        await self._plugin.data_handler.save_data()
        return jsonify({"ok": True})

    async def _pause_sub(self):
        data = await request.get_json()
        user = data.get("user", "")
        url = data.get("url", "")
        if not url:
            idx = data.get("idx", -1)
            subs_urls = self._plugin.data_handler.get_subs_channel_url(user)
            if idx < 0 or idx >= len(subs_urls):
                return jsonify({"error": "索引越界"}), 404
            url = subs_urls[idx]
        self._plugin.data_handler.data[url]["subscribers"][user]["paused"] = True
        await self._plugin.data_handler.save_data()
        return jsonify({"ok": True})

    async def _resume_sub(self):
        data = await request.get_json()
        user = data.get("user", "")
        url = data.get("url", "")
        if not url:
            idx = data.get("idx", -1)
            subs_urls = self._plugin.data_handler.get_subs_channel_url(user)
            if idx < 0 or idx >= len(subs_urls):
                return jsonify({"error": "索引越界"}), 404
            url = subs_urls[idx]
        self._plugin.data_handler.data[url]["subscribers"][user]["paused"] = False
        await self._plugin.data_handler.save_data()
        return jsonify({"ok": True})

    async def _stats(self):
        data = self._plugin.data_handler.data
        feeds = {k: v for k, v in data.items()
                 if k not in ("rsshub_endpoints", "settings") and not k.startswith("_")
                 and "subscribers" in v}
        total_subs = sum(len(v["subscribers"]) for v in feeds.values())
        total_feeds = len(feeds)
        paused = sum(1 for v in feeds.values()
                     for s in v["subscribers"].values() if s.get("paused"))
        history = data.get("_push_history", [])
        failed = data.get("_failed_pushes", [])
        return jsonify({
            "feeds": total_feeds,
            "subscriptions": total_subs,
            "paused": paused,
            "active": total_subs - paused,
            "endpoints": len(data.get("rsshub_endpoints", [])),
            "push_history": len(history),
            "failed_pushes": len(failed),
            "jobs": len(self._plugin.scheduler.get_jobs()),
        })

    async def _update_sub(self):
        data = await request.get_json()
        user = data.get("user", "")
        url = data.get("url", "")
        cron = data.get("cron", "0 * * * *")
        renderer = data.get("renderer")
        new_user = data.get("new_user", "")
        if not user:
            return jsonify({"error": "缺少 user 参数"}), 400
        if not url:
            idx = data.get("idx", -1)
            subs_urls = self._plugin.data_handler.get_subs_channel_url(user)
            if idx < 0 or idx >= len(subs_urls):
                return jsonify({"error": "索引越界"}), 404
            url = subs_urls[idx]

        if new_user and new_user != user:
            # 转移订阅到新用户/群聊
            old_sub = self._plugin.data_handler.data[url]["subscribers"].pop(user, None)
            if old_sub is None:
                return jsonify({"error": "订阅不存在"}), 404
            self._plugin.data_handler.data[url]["subscribers"][new_user] = old_sub
            self._plugin._remove_single_job(url, user)
            user = new_user

        sub = self._plugin.data_handler.data[url]["subscribers"][user]
        sub["cron_expr"] = cron
        if renderer:
            sub["renderer"] = renderer
        elif renderer == "" and "renderer" in sub:
            del sub["renderer"]
        # 更新模式 + 过滤设置
        if "update_mode" in data:
            sub["update_mode"] = data["update_mode"]
        if "filter_mode" in data:
            sub["filter_mode"] = data["filter_mode"]
        if "filter_blacklist" in data:
            sub["filter_blacklist"] = data["filter_blacklist"]
        if "filter_whitelist" in data:
            sub["filter_whitelist"] = data["filter_whitelist"]
        await self._plugin.data_handler.save_data()
        self._plugin._remove_single_job(url, user)
        self._plugin._add_single_job(url, user, cron)
        return jsonify({"ok": True})

    async def _fetch_items(self):
        data = await request.get_json()
        user = data.get("user", "")
        url = data.get("url", "")
        if not user:
            return jsonify({"error": "缺少 user 参数"}), 400
        if not url:
            idx = data.get("idx", -1)
            subs_urls = self._plugin.data_handler.get_subs_channel_url(user)
            if idx < 0 or idx >= len(subs_urls):
                return jsonify({"error": "索引越界"}), 404
            url = subs_urls[idx]
        try:
            items = await self._plugin.fetcher.poll(url)
        except Exception as e:
            return jsonify({"error": f"拉取失败: {e}"}), 500
        result = []
        for item in items:
            result.append({
                "title": item.title,
                "link": item.link,
                "description": item.description[:300],
                "pubDate": item.pubDate,
                "timestamp": item.pubDate_timestamp,
                "pic_urls": item.pic_urls,
            })
        result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return jsonify({"url": url, "count": len(result), "items": result})

    # ── RSSHub Endpoints ──────────────────────────────────

    async def _list_endpoints(self):
        eps = self._plugin.data_handler.data.get("rsshub_endpoints", [])
        return jsonify([{"idx": i, "url": u} for i, u in enumerate(eps)])

    async def _add_endpoint(self):
        data = await request.get_json()
        url = data.get("url", "").rstrip("/")
        if not url:
            return jsonify({"error": "URL 不能为空"}), 400
        url = self._plugin.fetcher.normalize_url(url)
        if url in self._plugin.data_handler.data.get("rsshub_endpoints", []):
            return jsonify({"error": "该端点已存在"}), 409
        self._plugin.data_handler.data["rsshub_endpoints"].append(url)
        await self._plugin.data_handler.save_data()
        return jsonify({"ok": True})

    async def _del_endpoint(self):
        data = await request.get_json()
        idx = data.get("idx", -1)
        eps = self._plugin.data_handler.data.get("rsshub_endpoints", [])
        if idx < 0 or idx >= len(eps):
            return jsonify({"error": "索引越界"}), 404
        eps.pop(idx)
        await self._plugin.data_handler.save_data()
        return jsonify({"ok": True})

    # ── Config ────────────────────────────────────────────

    async def _get_config(self):
        cfg = self._plugin.config
        return jsonify(dict(cfg) if hasattr(cfg, "items") else {})

    async def _update_config(self):
        data = await request.get_json()
        allowed = {"title_max_length","description_max_length","max_items_per_poll",
                    "max_consecutive_failures","compose","t2i","is_hide_url","verify_ssl","proxy"}
        for key, value in data.items():
            if key not in allowed:
                continue
            self._plugin.config[key] = value
        self._plugin.data_handler.data.setdefault("settings", {})
        saved = self._plugin.data_handler.data["settings"].setdefault("config", {})
        saved.update(data)
        await self._plugin.data_handler.save_data()
        return jsonify({"ok": True, "note": "部分配置可能需要重启插件后生效"})

    async def _reload_scheduler(self):
        self._plugin._fresh_asyncIOScheduler()
        count = len(self._plugin.scheduler.get_jobs())
        return jsonify({"ok": True, "jobs": count})

    # ── History ───────────────────────────────────────────

    async def _list_history(self):
        from datetime import datetime, timezone, timedelta
        user = request.args.get("user", "")
        history = self._plugin.data_handler.data.get("_push_history", [])
        if user:
            history = [h for h in history if h.get("user") == user]
        count = min(int(request.args.get("count", 20)), 100)
        recent = history[-count:]
        tz = timezone(timedelta(hours=8))
        items = []
        for entry in reversed(recent):
            items.append({
                "time": datetime.fromtimestamp(entry["time"], tz=tz)
                         .strftime("%Y-%m-%d %H:%M:%S"),
                "chan_title": entry["chan_title"],
                "title": entry["title"],
                "url": entry["url"],
                "user": entry["user"],
            })
        return jsonify({"total": len(history), "items": items})

    async def _list_logs(self):
        level = request.args.get("level", "")
        count = min(int(request.args.get("count", 100)), 300)
        try:
            logs = self._plugin.log_handler.list(level, count)
        except AttributeError:
            logs = []
        return jsonify({"items": logs})
