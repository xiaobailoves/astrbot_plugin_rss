from PIL import Image
import aiohttp
import random
import base64
from io import BytesIO

class RssImageHandler:
    """rss处理图片的类"""
    def __init__(self, is_adjust_pic=False):
        """
        初始化图片处理类

        Args:
            is_adjust_pic (bool): 是否防和谐，默认为 False。
        """
        self.is_adjust_pic = is_adjust_pic

    def _proxy_image_url(self, url: str) -> str:
        """为海外被墙图片添加反代/缓存加速"""
        # 常见国内无法直接访问的图片域名
        blocked_domains = [
            "twimg.com", "pbs.twimg.com", "twitter.com", 
            "pximg.net", "pixiv.net",
            "ytimg.com", "youtube.com",
            "telegram.org", "t.me",
            "instagram.com", "fbcdn.net"
        ]
        if any(domain in url for domain in blocked_domains):
            # 去掉协议头，交给公共缓存加速节点代理
            clean_url = url.replace("https://", "").replace("http://", "")
            proxied_url = f"https://wsrv.nl/?url={clean_url}"
            print(f"触发海外图片反代: {url} -> {proxied_url}")
            return proxied_url
        return url

    async def modify_corner_pixel_to_base64(self, image_url, color=(255, 255, 255)):
        """
        从URL读取图片，修改四个角的其中一个像素点为指定颜色，并以 Base64 编码字符串输出。

        Args:
            image_url (str): 图片的URL地址。
            color (tuple): 一个包含 RGB 值的元组，默认为 (255, 255, 255) 白色。

        Returns:
            str: 修改后图片的 Base64 编码字符串，如果发生错误则返回 None。
        """
        # ==== 代理魔法：在真正发起请求前，转换海外图片链接 ====
        actual_url = self._proxy_image_url(image_url)
        # ======================================================

        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                # 注意这里请求的是 actual_url 而不是原始的 image_url
                async with session.get(actual_url) as resp:
                    if resp.status != 200:
                        print(f"错误：无法从URL '{actual_url}' 获取图片: 状态码 {resp.status}")
                        return None

                    img_data = BytesIO(await resp.read())

                    if self.is_adjust_pic:
                        img = Image.open(img_data)
                        # 将图片转换为 RGB 模式，确保可以保存为 jpg
                        img = img.convert("RGB")
                        width, height = img.size
                        pixels = img.load()

                        # 随机选择四个角落之一
                        corners = [
                            (0, 0),                  # 左上角
                            (width - 1, 0),          # 右上角
                            (0, height - 1),         # 左下角
                            (width - 1, height - 1)  # 右下角
                        ]

                        # 随机选择一个角落
                        chosen_corner = random.choice(corners)
                        pixels[chosen_corner[0], chosen_corner[1]] = color

                        # 将修改后的图片保存到内存中的 BytesIO 对象
                        output_buffer = BytesIO()

                        img.save(output_buffer, format="JPEG")  # 可以选择其他格式，如 PNG
                        output_buffer.seek(0)

                        # 将内存中的图片数据编码为 Base64 字符串
                        base64_string = base64.b64encode(output_buffer.read()).decode("utf-8")
                        return base64_string
                    else:
                        # 如果不需要修改图片，直接返回原始图片的 Base64 编码
                        base64_string = base64.b64encode(img_data.getvalue()).decode("utf-8")
                        return base64_string

        except aiohttp.ClientError as e:
            print(f"错误：无法从URL '{actual_url}' 获取图片: {e}")
            return None
        except Exception as e:
            print(f"发生错误：{e}")
            return None