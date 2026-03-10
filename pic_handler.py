from PIL import Image
import aiohttp
import random
import base64
from io import BytesIO

class RssImageHandler:
    """RSS 图片处理类，支持通过代理或反代获取图片"""
    
    def __init__(self, is_adjust_pic=False, proxy=None, use_twitter_reverse_proxy=False, twitter_reverse_proxy_domain="pbs.yurucamp.cn"):
        """
        初始化图片处理类

        Args:
            is_adjust_pic (bool): 是否修改像素点（防和谐）。
            proxy (str): 代理地址，例如 'http://127.0.0.1:7890'。
            use_twitter_reverse_proxy (bool): 是否启用推特图片反代
            twitter_reverse_proxy_domain (str): 推特图片反代域名
        """
        self.is_adjust_pic = is_adjust_pic
        self.proxy = proxy
        self.use_twitter_reverse_proxy = use_twitter_reverse_proxy
        self.twitter_reverse_proxy_domain = twitter_reverse_proxy_domain

    async def modify_corner_pixel_to_base64(self, image_url, color=(255, 255, 255)):
        """
        从 URL 读取图片，支持代理/反代获取，并可选修改像素点输出 Base64。

        Args:
            image_url (str): 图片的 URL 地址。
            color (tuple): 像素点颜色。

        Returns:
            str: Base64 字符串。
        """
        request_proxy = self.proxy

        # 检查是否开启推特反代，并且链接是推特图片CDN
        if self.use_twitter_reverse_proxy and "pbs.twimg.com" in image_url:
            image_url = image_url.replace("pbs.twimg.com", self.twitter_reverse_proxy_domain)
            # 使用反代时通常为了速度会选择直连，强制将此次请求的 proxy 置空
            request_proxy = None 

        try:
            # 使用 trust_env=True 以便识别系统环境变量中的代理，或者手动传入 request_proxy 参数
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(image_url, proxy=request_proxy, timeout=30) as resp:
                    if resp.status != 200:
                        print(f"图片下载失败: {image_url}, 状态码: {resp.status}")
                        return None

                    content = await resp.read()
                    img_data = BytesIO(content)

                    if self.is_adjust_pic:
                        img = Image.open(img_data)
                        img = img.convert("RGB")
                        width, height = img.size
                        pixels = img.load()

                        # 随机修改一个角
                        corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
                        chosen = random.choice(corners)
                        pixels[chosen[0], chosen[1]] = color

                        output_buffer = BytesIO()
                        img.save(output_buffer, format="JPEG")
                        return base64.b64encode(output_buffer.getvalue()).decode("utf-8")
                    else:
                        return base64.b64encode(content).decode("utf-8")

        except Exception as e:
            print(f"图片处理异常 ({image_url}): {e}")
            return None