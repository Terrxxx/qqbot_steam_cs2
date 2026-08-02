from __future__ import annotations

import base64
import io
import httpx
import os
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from openai import AsyncOpenAI
from services.cos_service import COSService
from utils.logger import logger
from config import config

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

class ImageService:
    """图片处理服务"""

    @staticmethod
    async def generate_via_ai(prompt: str, api_key: str = config.ai.img_api_key, api_base: str = config.ai.img_api_base, size: str = "1024x1024") -> Optional[bytes]:
        """AI 生成图片，返回 raw bytes"""
        client = AsyncOpenAI(api_key=api_key, base_url=api_base)

        try:
            response = await client.images.generate(
                model=config.ai.img_model,
                prompt=prompt,
                size=size,
                quality="auto",
                n=1,
            )
        except Exception as e:
            logger.error(f"AI 图片生成 API 调用失败: {type(e).__name__}: {e}")
            return None

        image_obj = response.data[0]
        image_url = getattr(image_obj, "url", None) or ""
        b64_data = getattr(image_obj, "b64_json", None) or ""

        logger.info(f"图片 API 响应: url={'有' if image_url else '无'} b64_len={len(b64_data)}")

        if image_url and image_url.startswith("http"):
            image_bytes = await ImageService._download_image(image_url)
            if image_bytes:
                logger.info(f"图片下载成功: {len(image_bytes)} bytes")
                return image_bytes

        if b64_data:
            image_bytes = ImageService._decode_base64(b64_data)
            if image_bytes:
                logger.info(f"base64 解码成功: {len(image_bytes)} bytes")
                return image_bytes

        logger.error("图片生成失败：无可用 URL 且无 base64 数据")
        return None

    @staticmethod
    async def generate_and_upload(prompt: str, api_key: str = config.ai.img_api_key, api_base: str = config.ai.img_api_base,) -> Optional[str]:
        """AI 生成图片 → 格式转换 → COS 上传 → 返回公网 URL"""
        raw = await ImageService.generate_via_ai(prompt, api_key, api_base)
        if not raw:
            return None

        jpeg = ImageService.bytes_to_jpeg(raw)
        if not jpeg:
            return None

        url = ImageService.upload_to_cos(jpeg, content_type="image/jpeg")
        if url:
            logger.info(f"图片全流程完成: {url}")
        return url

    # ==================== 内部工具 ====================

    @staticmethod
    def upload_to_cos(image_bytes: bytes, content_type: str = "image/jpeg") -> Optional[str]:
        """上传到腾讯云 COS"""
        cos = COSService.get_instance()
        return cos.upload_image(image_bytes, content_type=content_type)

    @staticmethod
    async def _download_image(url: str, timeout: int = 30) -> Optional[bytes]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as cli:
                resp = await cli.get(url)
                if resp.status_code == 200 and len(resp.content) > 0:
                    return resp.content
                logger.warning(f"下载失败: HTTP {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"下载异常: {e}")
            return None

    @staticmethod
    def _decode_base64(b64_str: str) -> Optional[bytes]:
        if "," in b64_str and b64_str.startswith("data:"):
            b64_str = b64_str.split(",", 1)[1]
        try:
            return base64.b64decode(b64_str)
        except Exception as e:
            logger.error(f"base64 解码失败: {e}")
            return None

    @staticmethod
    def bytes_to_jpeg(image_bytes: bytes, max_size: int = 4 * 1024 * 1024) -> Optional[bytes]:
        """将任意格式图片转为 JPEG，自动压缩到 max_size 以内"""
        try:
            img = Image.open(io.BytesIO(image_bytes))

            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                mask = img.split()[-1] if img.mode == "RGBA" else None
                bg.paste(img, mask=mask)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            quality = 85
            while True:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                data = buf.getvalue()
                if len(data) <= max_size or quality <= 30:
                    logger.info(
                        f"图片转换: {len(image_bytes)} → {len(data)} bytes "
                        f"(JPEG q={quality})"
                    )
                    return data
                quality -= 10
                if img.width > 1024:
                    ratio = 1024 / img.width
                    img = img.resize(
                        (1024, int(img.height * ratio)), Image.LANCZOS
                    )

        except Exception as e:
            logger.error(f"图片格式转换失败: {e}")
            return image_bytes

    # ==================== Steam 状态卡片 ====================

    @staticmethod
    def _load_image(src: str, size: tuple | None = None) -> "Image.Image | None":
        """加载图片：优先本地路径，否则 HTTP 下载"""
        if not src:
            return None
        try:
            if Path(src).exists():
                img = Image.open(src)
            elif src.startswith("http"):
                resp = httpx.get(src, timeout=8, verify=False)
                if resp.status_code != 200:
                    return None
                img = Image.open(io.BytesIO(resp.content))
            else:
                return None
            if size:
                img = img.resize(size)
            return img
        except Exception:
            return None

    @staticmethod
    def generate_status_card(
        avatar_url: str = "",
        player_name: str = "",
        status: str = "",
        game_name: str = "",
        game_logo_url: str = "",
        rich_presence: str = "",
        steam_level: str = "",
        played_minutes: str = "",
        frame_url: str = "",
        nameplate_url: str = "",
        badge_url: str = "",
        badge_name: str = "",
        badge_xp: str = "",
    ) -> bytes:
        """生成 Steam 状态卡片"""
        W, H = 984, 628
        dark_bg = (26, 29, 45)
        dark_panel = (28, 31, 42)
        text_white = (255, 255, 255)
        text_gray = (140, 145, 155)
        green = (100, 200, 120)
        orange = (220, 160, 90)
        blue = (100, 160, 240)
        black = (0, 0, 0)
        online_blue = (87, 203, 222)   # #57CBDE
        ingame_green = (144, 186, 60)  # #90BA3C

        img = Image.new("RGB", (W, H), color=dark_bg)
        draw = ImageDraw.Draw(img)

        # 字体
        font_paths = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]
        bold_paths = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf"]
        font_name, font_status, font_info, font_small = None, None, None, None
        font_name_bold, font_status_bold = None, None
        for fp in font_paths:
            if Path(fp).exists():
                try:
                    font_name = ImageFont.truetype(fp, 56)
                    font_status = ImageFont.truetype(fp, 32)
                    font_info = ImageFont.truetype(fp, 30)
                    font_small = ImageFont.truetype(fp, 24)
                    font_very_small = ImageFont.truetype(fp, 18)
                except Exception:
                    continue
                break
        for fp in bold_paths:
            if Path(fp).exists():
                try:
                    font_name_bold = ImageFont.truetype(fp, 56)
                    font_status_bold = ImageFont.truetype(fp, 32)
                except Exception:
                    continue
                break
        if font_name is None:
            font_name = font_status = font_info = font_small = ImageFont.load_default()
        if font_name_bold is None:
            font_name_bold, font_status_bold = font_name, font_status

        # 迷你背景填充整卡
        nameplate_img = ImageService._load_image(nameplate_url)
        avatar_img = ImageService._load_image(avatar_url)
        frame_img = ImageService._load_image(frame_url, (124, 124))

        top_h = 300

        if nameplate_img:
            nw, nh = nameplate_img.size
            scale = max(W / nw, H / nh)
            scaled = nameplate_img.resize((int(nw * scale), int(nh * scale))).convert("RGBA")
            left = (scaled.width - W) // 2
            top_crop = (scaled.height - H) // 2
            full_bg = scaled.crop((left, top_crop, left + W, top_crop + H)).convert("RGB")
            img.paste(full_bg, (0, 0))
        else:
            for i in range(H):
                r = int(30 + 20 * i / H)
                g = int(32 + 25 * i / H)
                b = int(55 + 30 * i / H)
                draw.line([(0, i), (W, i)], fill=(r, g, b))

        # 下部信息栏
        bot_h = H - top_h
        if nameplate_img:
            bot_bg = full_bg.crop((0, top_h, W, H)).resize((W, bot_h))
            bot_bg = bot_bg.filter(ImageFilter.GaussianBlur(radius=12))
            img.paste(bot_bg.convert("RGB"), (0, top_h))
        else:
            draw.rectangle([0, top_h, W, H], fill=dark_panel)

        panel = Image.new("RGBA", (W, bot_h), (*dark_panel, 180))
        img.paste(panel.convert("RGB"), (0, top_h), panel)

        # 左上头像
        avatar_size = 310
        avatar_y = 36
        avatar_x = 36
        if avatar_img:
            thumb = avatar_img.resize((avatar_size, avatar_size)).convert("RGBA")
            mask = Image.new("L", (avatar_size, avatar_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([0, 0, avatar_size, avatar_size], radius=4, fill=255)
            img.paste(thumb, (avatar_x, avatar_y), mask)
            if frame_img:
                fr = frame_img.resize((avatar_size + 72, avatar_size + 72)).convert("RGBA")
                img.paste(fr, (avatar_x - 36, avatar_y - 36), fr)
        else:
            draw.rounded_rectangle(
                [avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size],
                radius=4, fill=black,
            )
            draw.text((avatar_x + avatar_size // 2, avatar_y + avatar_size // 2),
                      "?", fill=text_white, font=font_name, anchor="mm")

        # 上部文字
        text_x = avatar_x + avatar_size + 28
        text_y = avatar_y + 12
        offline_gray = (160, 160, 160)  # #A0A0A0
        status_colors = {"在线": online_blue, "游戏中": ingame_green, "离开": orange, "离线": offline_gray}
        sc = status_colors.get(status, offline_gray)

        # 模糊阴影
        def _draw_text_with_blur(draw_target, xy, text, fill_color, font, img_target, blur_radius=3):
            """在独立层画黑色文字 → 高斯模糊 → 合成 → 再画彩色文字"""
            shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow_layer)
            sd.text(xy, text, fill=(0, 0, 0, 220), font=font)
            # 模糊
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            img_target.paste(shadow_layer, (0, 0), shadow_layer)
            draw_target.text(xy, text, fill=fill_color, font=font)

        _draw_text_with_blur(draw, (text_x, text_y), player_name, sc, font=font_name_bold, img_target=img)
        name_bbox = draw.textbbox((text_x, text_y), player_name, font=font_name_bold)
        sy = name_bbox[3] + 12
        _draw_text_with_blur(draw, (text_x, sy), status, sc, font=font_status_bold, img_target=img)

        # 游戏信息 (状态下方)
        game_y = sy + 48
        if game_name:
            _draw_text_with_blur(draw, (text_x, game_y), game_name, ingame_green, font=font_info, img_target=img)
            # if rich_presence:
            #     _draw_text_with_blur(draw, (text_x, game_y + 38), rich_presence[:60], text_gray, font=font_small, img_target=img)
            # 游戏 Logo (保持比例, 高度64)
            if game_logo_url:
                logo_img = ImageService._load_image(game_logo_url)
                if logo_img:
                    logo_h = 128
                    ratio = logo_h / logo_img.height
                    logo_w = int(logo_img.width * ratio)
                    logo = logo_img.resize((logo_w, logo_h))
                    img.paste(logo, (text_x, game_y + 50))

        # 等级 + 勋章 (头像下方, 左上角纵列)
        lv_x = avatar_x + 32
        lv_y = avatar_y + avatar_size + 16

        # 等级徽章
        if steam_level:
            try: lv = int(steam_level)
            except: lv = 0

            # 非Steam原版的等级样式，随机出几个颜色和样式了
            if lv < 10:    lv_color = (140, 145, 155)
            elif lv < 20:  lv_color = (80, 180, 80)
            elif lv < 30:  lv_color = (70, 140, 220)
            elif lv < 40:  lv_color = (140, 100, 220)
            elif lv < 50:  lv_color = (220, 130, 180)
            else:          lv_color = (220, 180, 70)

            lv_size = 84
            lv_text = str(lv)
            try:
                lv_font = ImageFont.truetype(font_paths[0], 44)
            except:
                lv_font = font_small

            if lv <= 100:
                draw.ellipse([lv_x, lv_y + 8, lv_x + lv_size, lv_y + lv_size + 8],
                             outline=lv_color, width=6)
            else:
                draw.rounded_rectangle([lv_x, lv_y, lv_x + lv_size, lv_y + lv_size],
                                       radius=10, outline=lv_color, width=6)

            n_bb = draw.textbbox((0, 0), lv_text, font=lv_font)
            n_w, n_h = n_bb[2] - n_bb[0], n_bb[3] - n_bb[1]
            draw.text((lv_x + lv_size // 2 - n_w // 2,
                       lv_y + lv_size // 2 - n_h // 2 - 2),
                      lv_text, fill=text_white, font=lv_font)
            draw.text((lv_x + lv_size + 10, lv_y + lv_size // 2 - n_h // 2 - 2),
                      " Steam 等级", fill=text_white, font=lv_font)

        # 勋章 (等级下方)
        if badge_url:
            badge_by = lv_y + 130
            badge_img = ImageService._load_image(badge_url, (78, 78))
            if badge_img:
                img.paste(badge_img, (lv_x, badge_by),
                        badge_img.convert("RGBA") if badge_img.mode == "RGBA" else None)
            draw.text((lv_x + 96, badge_by), badge_name or "", fill=text_white, font=font_small)
            if badge_xp:
                draw.text((lv_x + 96, badge_by + 36), badge_xp, fill=text_gray, font=font_small)

        # 底部分割线
        draw.line([(40, H - 38), (220, H - 38)], fill=(48, 50, 62), width=2)
        draw.text((50, H - 30), "Steam Bot@Terrxx", fill=(60, 62, 75), font=font_very_small)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()
