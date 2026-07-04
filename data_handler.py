import os
import json
import tempfile
import logging
from urllib.parse import urlparse
from lxml import etree
from bs4 import BeautifulSoup
import re

class DataHandler:
    def __init__(self, config_path="data/astrbot_plugin_rss_data.json", default_config=None):
        self.config_path = config_path
        self.default_config = default_config or {
            "rsshub_endpoints": []
        }
        self.data = self.load_data()

    def get_subs_channel_url(self, user_id) -> list:
        """获取用户订阅的频道 url 列表"""
        subs_url = []
        for url, info in self.data.items():
            if url == "rsshub_endpoints" or url == "settings":
                continue
            if user_id in info["subscribers"]:
                subs_url.append(url)
        return subs_url

    def load_data(self):
        """从数据文件中加载数据"""
        if not os.path.exists(self.config_path):
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.default_config, f, indent=2, ensure_ascii=False)
            return self.default_config.copy()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger = logging.getLogger("astrbot")
            logger.error(f"数据文件 {self.config_path} 已损坏，正在备份并重建...")
            backup_path = self.config_path + ".corrupted_backup"
            os.replace(self.config_path, backup_path)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.default_config, f, indent=2, ensure_ascii=False)
            return self.default_config.copy()

    def save_data(self):
        """保存数据到数据文件（原子写入，防止崩溃时文件损坏）"""
        dirname = os.path.dirname(self.config_path) or "."
        with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=dirname, suffix=".tmp", encoding="utf-8"
        ) as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
            tmp_path = f.name
        os.replace(tmp_path, self.config_path)

    def parse_channel_text_info(self, text):
        """解析RSS频道信息"""
        try:
            root = etree.fromstring(text)
            title_node = root.xpath("//title")
            desc_node = root.xpath("//description")
            title = title_node[0].text if title_node and title_node[0].text else "未知频道"
            description = desc_node[0].text if desc_node and desc_node[0].text else ""
            return title, description
        except (etree.XMLSyntaxError, IndexError, AttributeError):
            return "未知频道", ""

    def parse_html_text_and_pics(self, html) -> tuple:
        """单次解析 HTML，同时提取纯文本和图片地址"""
        soup = BeautifulSoup(html, "html.parser")
        ordered_content = []
        for img in soup.find_all("img"):
            img_src = img.get("src")
            if img_src:
                ordered_content.append(img_src)
        text = soup.get_text()
        return re.sub(r"\n+", "\n", text), ordered_content

    def strip_html_pic(self, html)-> list[str]:
        """解析HTML内容，提取图片地址（已废弃，保留兼容）"""
        _, pics = self.parse_html_text_and_pics(html)
        return pics

    def strip_html(self, html):
        """去除HTML标签（已废弃，保留兼容）"""
        text, _ = self.parse_html_text_and_pics(html)
        return text

    def get_root_url(self, url):
        """获取URL的根域名"""
        parsed_url = urlparse(url)
        return f"{parsed_url.scheme}://{parsed_url.netloc}"
