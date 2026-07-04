"""RSS 插件 WebUI - aiohttp HTTP API 服务"""
import json
import urllib.parse
from aiohttp import web

STATIC_DIR = __import__("os").path.join(
    __import__("os").path.dirname(__file__), "webui"
)


class RssWebUI:
    """RSS 插件 Web 管理后台"""

    def __init__(self, plugin, host="127.0.0.1", port=8888):
        self.plugin = plugin
        self.host = host
        self.port = port
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self._serve_index)
        self.app.router.add_get("/api/rsshub", self._list_endpoints)
        self.app.router.add_post("/api/rsshub", self._add_endpoint)
        self.app.router.add_delete("/api/rsshub/{idx}", self._del_endpoint)
        self.app.router.add_get("/api/subscriptions", self._list_subscriptions)
        self.app.router.add_get("/api/subscriptions/all", self._list_all_subs)
        self.app.router.add_post("/api/subscriptions/rsshub", self._add_sub_rsshub)
        self.app.router.add_post("/api/subscriptions/url", self._add_sub_url)
        self.app.router.add_delete("/api/subscriptions/{idx}", self._del_subscription)
        self.app.router.add_put("/api/subscriptions/{idx}", self._update_subscription)
        self.app.router.add_post(
            "/api/subscriptions/{idx}/fetch", self._fetch_subscription
        )
        self.app.router.add_get("/api/config", self._get_config)
        self.app.router.add_put("/api/config", self._update_config)
        self.app.router.add_post("/api/reload", self._reload_scheduler)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self.plugin.logger.info(
            f"RSS WebUI 已启动 → http://{self.host}:{self.port}"
        )

    # ── 静态页面 ──────────────────────────────────────────

    async def _serve_index(self, request):
        path = STATIC_DIR + "/index.html"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/html")
        except FileNotFoundError:
            return web.Response(text="WebUI not found", status=404)

    # ── JSON 工具 ─────────────────────────────────────────

    @staticmethod
    def _json(data, status=200):
        return web.Response(
            text=json.dumps(data, ensure_ascii=False),
            content_type="application/json",
            status=status,
        )

    @staticmethod
    async def _body(request):
        try:
            return await request.json()
        except Exception:
            return {}

    # ── 端点管理 ──────────────────────────────────────────

    async def _list_endpoints(self, request):
        eps = self.plugin.data_handler.data.get("rsshub_endpoints", [])
        return self._json([{"idx": i, "url": u} for i, u in enumerate(eps)])

    async def _add_endpoint(self, request):
        body = await self._body(request)
        url = body.get("url", "").rstrip("/")
        if not url:
            return self._json({"error": "URL 不能为空"}, 400)
        url = self.plugin.parse_rss_url(url)
        if url in self.plugin.data_handler.data.get("rsshub_endpoints", []):
            return self._json({"error": "该端点已存在"}, 409)
        self.plugin.data_handler.data["rsshub_endpoints"].append(url)
        self.plugin.data_handler.save_data()
        return self._json({"ok": True})

    async def _del_endpoint(self, request):
        idx = int(request.match_info["idx"])
        eps = self.plugin.data_handler.data.get("rsshub_endpoints", [])
        if idx < 0 or idx >= len(eps):
            return self._json({"error": "索引越界"}, 404)
        eps.pop(idx)
        self.plugin.data_handler.save_data()
        return self._json({"ok": True})

    # ── 订阅管理 ──────────────────────────────────────────

    def _get_user_subs(self, user):
        """返回某用户的订阅列表 [(url, info, sub_info), ...]"""
        dh = self.plugin.data_handler
        result = []
        for url, info in dh.data.items():
            if url in ("rsshub_endpoints", "settings"):
                continue
            if user in info.get("subscribers", {}):
                result.append(
                    {
                        "url": url,
                        "title": info.get("info", {}).get("title", "未知频道"),
                        "desc": info.get("info", {}).get("description", ""),
                        "cron": info["subscribers"][user].get("cron_expr", ""),
                        "last_update": info["subscribers"][user].get("last_update", 0),
                    }
                )
        return result

    async def _list_all_subs(self, request):
        dh = self.plugin.data_handler
        result = []
        for url, info in dh.data.items():
            if url in ("rsshub_endpoints", "settings"):
                continue
            for user, sub in info.get("subscribers", {}).items():
                result.append({
                    "user": user,
                    "url": url,
                    "title": info.get("info", {}).get("title", "unknown"),
                    "desc": info.get("info", {}).get("description", ""),
                    "cron": sub.get("cron_expr", ""),
                    "last_update": sub.get("last_update", 0),
                })
        return self._json(result)

    async def _list_subscriptions(self, request):
        user = request.query.get("user", "")
        if not user:
            return self._json({"error": "缺少 user 参数"}, 400)
        subs = self._get_user_subs(user)
        return self._json(subs)

    async def _add_sub_rsshub(self, request):
        body = await self._body(request)
        user = body.get("user", "")
        ep_idx = body.get("endpoint_idx", -1)
        route = body.get("route", "")
        cron = body.get("cron", "0 * * * *")
        if not user or not route:
            return self._json({"error": "user / route 不能为空"}, 400)
        eps = self.plugin.data_handler.data.get("rsshub_endpoints", [])
        if ep_idx < 0 or ep_idx >= len(eps):
            return self._json({"error": "端点索引无效"}, 400)
        url = eps[ep_idx] + route

        try:
            info = await self.plugin._add_url(url, cron, _FakeEvent(user))
        except Exception as e:
            return self._json({"error": f"添加失败: {e}"}, 500)

        self.plugin._add_single_job(url, user, cron)
        return self._json({"ok": True, "url": url, "title": info["title"]})

    async def _add_sub_url(self, request):
        body = await self._body(request)
        user = body.get("user", "")
        url = body.get("url", "")
        cron = body.get("cron", "0 * * * *")
        if not user or not url:
            return self._json({"error": "user / url 不能为空"}, 400)
        url = self.plugin.parse_rss_url(url)

        try:
            info = await self.plugin._add_url(url, cron, _FakeEvent(user))
        except Exception as e:
            return self._json({"error": f"添加失败: {e}"}, 500)

        self.plugin._add_single_job(url, user, cron)
        return self._json({"ok": True, "url": url, "title": info["title"]})

    async def _del_subscription(self, request):
        idx = int(request.match_info["idx"])
        user = request.query.get("user", "")
        if not user:
            return self._json({"error": "缺少 user 参数"}, 400)
        subs_urls = self.plugin.data_handler.get_subs_channel_url(user)
        if idx < 0 or idx >= len(subs_urls):
            return self._json({"error": "索引越界"}, 404)
        url = subs_urls[idx]
        self.plugin.data_handler.data[url]["subscribers"].pop(user, None)
        if not self.plugin.data_handler.data[url]["subscribers"]:
            self.plugin.data_handler.data.pop(url)
        self.plugin._remove_single_job(url, user)
        self.plugin.data_handler.save_data()
        return self._json({"ok": True})

    async def _update_subscription(self, request):
        idx = int(request.match_info["idx"])
        user = request.query.get("user", "")
        body = await self._body(request)
        cron = body.get("cron", "0 * * * *")
        if not user:
            return self._json({"error": "缺少 user 参数"}, 400)
        subs_urls = self.plugin.data_handler.get_subs_channel_url(user)
        if idx < 0 or idx >= len(subs_urls):
            return self._json({"error": "索引越界"}, 404)
        url = subs_urls[idx]
        self.plugin.data_handler.data[url]["subscribers"][user]["cron_expr"] = cron
        self.plugin.data_handler.save_data()
        self.plugin._remove_single_job(url, user)
        self.plugin._add_single_job(url, user, cron)
        return self._json({"ok": True})

    async def _fetch_subscription(self, request):
        idx = int(request.match_info["idx"])
        user = request.query.get("user", "")
        if not user:
            return self._json({"error": "缺少 user 参数"}, 400)
        subs_urls = self.plugin.data_handler.get_subs_channel_url(user)
        if idx < 0 or idx >= len(subs_urls):
            return self._json({"error": "索引越界"}, 404)
        url = subs_urls[idx]
        try:
            items = await self.plugin.poll_rss(url)
        except Exception as e:
            return self._json({"error": f"拉取失败: {e}"}, 500)
        # poll_rss 内部做了 reverse() 导致旧→新，这里翻回来确保新→旧
        items.reverse()
        result = []
        for item in items:
            result.append(
                {
                    "title": item.title,
                    "link": item.link,
                    "description": item.description[:300],
                    "pubDate": item.pubDate,
                    "timestamp": item.pubDate_timestamp,
                    "pic_urls": item.pic_urls,
                }
            )
        result.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return self._json({"url": url, "count": len(result), "items": result})

    # ── 配置管理 ──────────────────────────────────────────

    async def _get_config(self, request):
        cfg = self.plugin.config
        return self._json(dict(cfg) if hasattr(cfg, "items") else {})

    async def _reload_scheduler(self, request):
        self.plugin._fresh_asyncIOScheduler()
        count = len(self.plugin.scheduler.get_jobs())
        return self._json({"ok": True, "jobs": count})

    async def _update_config(self, request):
        body = await self._body(request)
        for key, value in body.items():
            self.plugin.config[key] = value
        self.plugin.data_handler.save_data()
        return self._json({"ok": True, "note": "部分配置可能需要重启插件后生效"})


class _FakeEvent:
    """模拟 AstrMessageEvent 用于复用 _add_url"""

    def __init__(self, unified_msg_origin):
        self.unified_msg_origin = unified_msg_origin

    def plain_result(self, msg):
        # 被 _add_url 当作错误返回时抛异常传给调用方
        raise Exception(msg)
