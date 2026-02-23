from PIL import Image
import aiohttp
import random
import base64
from io import BytesIO

class RssImageHandler:
    """RSS 图片处理类，支持通过代理获取图片"""
    
    def __init__(self, is_adjust_pic=False, proxy=None):
        """
        初始化图片处理类

        Args:
            is_adjust_pic (bool): 是否修改像素点（防和谐）。
            proxy (str): 代理地址，例如 'http://127.0.0.1:7890'。
        """
        self.is_adjust_pic = is_adjust_pic
        self.proxy = proxy

    async def modify_corner_pixel_to_base64(self, image_url, color=(255, 255, 255)):
        """
        从 URL 读取图片，支持代理获取，并可选修改像素点输出 Base64。

        Args:
            image_url (str): 图片的 URL 地址。
            color (tuple): 像素点颜色。

        Returns:
            str: Base64 字符串。
        """
        try:
            # 使用 trust_env=True 以便识别系统环境变量中的代理，或者手动传入 proxy 参数
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(image_url, proxy=self.proxy, timeout=30) as resp:
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