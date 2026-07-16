from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from services.database import Database
from utils.logger import logger
from pathlib import Path
from services.image_service import ImageService
from services.cos_service import COSService
from utils.markdown_utils import build_markdown
from services.scheduler_service import SchedulerService
from config import config
import cv2
import tempfile
import os
import httpx
import asyncio
import re

STEAM_BASE64 = 76561197960265728

PROFILE_URL = "https://steamcommunity.com/miniprofile/{steamid}?origin=https%3A%2F%2Fsteamcommunity.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cookie": f'{config.steam.cookie}'
}


def convert_steam_id(raw: str) -> tuple[str, int]:
    """Steam ID 转换: 输入 64 位或 32 位, 返回 (64位str, 32位int)"""
    try:
        num = int(raw.strip())
    except ValueError:
        return (raw.strip(), 0)
    if num > STEAM_BASE64:
        return (str(num), num - STEAM_BASE64)
    else:
        return (str(num + STEAM_BASE64), num)


class SteamMonitor:
    """Steam 游戏状态监控器（单例）"""

    _instance: Optional["SteamMonitor"] = None

    def __new__(cls) -> "SteamMonitor":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._api: Any = None
        self._db = Database()
        self._state: dict[str, dict] = {}
        self._running = False
        logger.info("Steam 监控服务已初始化")

    @classmethod
    def get_instance(cls) -> "SteamMonitor":
        return cls()

    # ==================== 公共接口 ====================

    def set_api(self, api: Any) -> None:
        self._api = api

    @property
    def enabled(self) -> bool:
        return True

    def start(self) -> None:
        if self._running:
            return
        scheduler = SchedulerService.get_scheduler()
        scheduler.add_job(
            self._poll, trigger="interval", seconds=config.steam.poll_interval,
            id="steam_monitor", replace_existing=True, misfire_grace_time=15,
        )
        self._running = True
        logger.info(f"Steam 监控已启动 (每{config.steam.poll_interval}s)")

    def stop(self) -> None:
        SchedulerService.remove_job("steam_monitor")
        self._running = False

    async def check_one(self, steam_id: str) -> Optional[dict]:
        return await self._fetch_profile(steam_id)

    # ==================== 定时轮询 ====================

    async def _poll(self) -> None:
        try:
            profiles = self._db.get_all_profiles()
            if not profiles:
                return
            sem = asyncio.Semaphore(config.steam.max_concurrent)

            async def fetch_one(profile: dict):
                async with sem:
                    player = await self._fetch_profile(profile["steam_id"])
                return profile, player

            tasks = [fetch_one(p) for p in profiles]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"爬取异常: {result}")
                    continue
                profile, player = result
                if player is None:
                    continue
                sid = profile["steam_id"]
                display = profile["display_name"] or profile["steam_name"] or sid
                group = profile["group_openid"]
                await self._process_player(sid, display, group, player)
        except Exception as e:
            logger.error(f"Steam 轮询异常: {e}")

    STATE_LABELS = {0: "离线", 1: "在线", 2: "离开"}

    async def _process_player(self, sid: str, display: str, group: str, player: dict) -> None:
        """对比游戏 + 在线状态变化，推送卡片图片"""
        now = datetime.now()
        game_name = player.get("game_name")
        personastate = player.get("personastate", 0)
        prev = self._state.get(sid, {})
        prev_game = prev.get("game_name", "")
        prev_state = prev.get("personastate", 0)
        session_id = prev.get("session_id")
        is_first = not prev

        # 状态文本
        player["status_text"] = "游戏中" if game_name else self.STATE_LABELS.get(personastate, "离线")

        # 更新昵称
        if player.get("personaname"):
            self._db.update_steam_name(sid, player["personaname"])
            self._db.update_display_name(sid, player["personaname"])
            display = player["personaname"]

        # 保存状态
        self._state[sid] = {
            "in_game": bool(game_name), "game_name": game_name,
            "personastate": personastate, "session_id": session_id,
            "start": prev.get("start"),
        }

        if is_first:
            logger.info(f"首次检测: {display} game={game_name}")
            if game_name:
                sid_db = self._db.start_session(sid, game_name, now)
                self._state[sid]["session_id"] = sid_db
                self._state[sid]["start"] = now
            return

        # 在线状态变化
        if personastate != prev_state and not game_name:
            logger.info(f"状态变化: {display}")
            await self._push_card(group, player, sid=sid)

        # 游戏开始/切换
        if game_name and game_name != prev_game:
            sid_db = self._db.start_session(sid, game_name, now)
            self._state[sid]["session_id"] = sid_db
            self._state[sid]["start"] = now
            extra = {}
            if prev_game and session_id:
                extra["played_minutes"] = str(self._db.end_session(session_id, now))
            await self._push_card(group, player, extra, sid)
            logger.info(f"游戏开始: {display} -> {game_name}")

        # 游戏结束
        elif not game_name and prev_game:
            mins = 0
            if session_id:
                mins = self._db.end_session(session_id, now)
            self._state[sid]["session_id"] = None
            self._state[sid]["start"] = None
            await self._push_card(group, player, {"played_minutes": str(mins), "prev_game": prev_game}, sid)
            logger.info(f"游戏结束: {display} <- {prev_game} ({mins}min)")

    # ==================== 页面爬取 ====================

    async def _fetch_profile(self, steam_id: str) -> Optional[dict]:
        """爬取 Steam miniprofile（32 位 ID）"""
        _, account_id = convert_steam_id(steam_id)
        url = PROFILE_URL.format(steamid=account_id)
        try:
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=True, verify=False
            ) as client:
                resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return None
            html = resp.text

            # 昵称: <span class="persona in-game"> 或 <span class="persona online"> 或 <span class="persona offline">
            name = ""
            m = re.search(r'<span class="persona (?:in-game|online|offline)">(.+?)</span>', html, re.DOTALL)
            if m:
                name = re.sub(r"\s+", " ", m.group(1)).strip()
                sub_m = re.search(r'<span class="secondaryname">(.+?)</span>', html, re.DOTALL)
                if sub_m:
                    name = re.sub(r"\s+", " ", sub_m.group(1)).strip()[1:-1]
            if not name:
                return None

            # 游戏名: <span class="miniprofile_game_name">
            game_name = None
            m = re.search(r'<span class="miniprofile_game_name">(.+?)</span>', html, re.DOTALL)
            if m:
                game_name = re.sub(r"\s+", " ", m.group(1)).strip()

            # 游戏 Logo: <img class="game_logo" src="...">
            game_logo = ""
            m = re.search(r'<img class="game_logo" src="(.+?)"', html, re.DOTALL)
            if m:
                game_logo = m.group(1).strip()

            # Steam 等级: <span class="friendPlayerLevelNum">
            steam_level = ""
            m = re.search(r'<span class="friendPlayerLevelNum">(.+?)</span>', html, re.DOTALL)
            if m:
                steam_level = re.sub(r"\s+", " ", m.group(1)).strip()

            # 迷你背景: miniprofile_nameplate video 第一个 source
            nameplate = ""
            np_m = re.search(
                r'<video class="miniprofile_nameplate"[^>]*>.*?<source src="(https://[^"]+)"',
                html, re.DOTALL,
            )
            if np_m:
                nameplate = np_m.group(1).strip()

            # 头像: playersection_avatar border_color_in-game div 内的 img src
            avatar_url = ""
            av_div = re.search(
                r'<div class="playersection_avatar border_color_.*?">(.*?)</div>',
                html, re.DOTALL,
            )
            if av_div:
                av_img = re.search(r'<img src="(https://[^"]+)"', av_div.group(1))
                if av_img:
                    avatar_url = av_img.group(1).strip().replace("medium", "full")

            # 头像框: playersection_avatar_frame div 内的 img src
            frame_url = ""
            frame_div = re.search(
                r'<div class="playersection_avatar_frame">(.*?)</div>',
                html, re.DOTALL,
            )
            if frame_div:
                frame_img = re.search(r'<img src="(https://[^"]+)"', frame_div.group(1))
                if frame_img:
                    frame_url = frame_img.group(1).strip()

            # ---- 缓存到本地 ----
            cache_dir = Path(__file__).resolve().parent.parent / "output" / "steam_cards" / "cache" / str(account_id)
            cache_dir.mkdir(parents=True, exist_ok=True)

            avatar = ""
            frame = ""
            nameplate_path = ""

            # ---- 缓存辅助: URL 变更则重新下载 ----
            def _need_update(path: str, new_url: str) -> bool:
                url_file = f"{path}.url"
                try:
                    old = Path(url_file).read_text().strip() if Path(url_file).exists() else ""
                    return old != new_url
                except Exception:
                    return True

            def _save_url(path: str, url: str):
                Path(f"{path}.url").write_text(url)

            # 迷你背景
            if nameplate:
                nameplate_path = str(cache_dir / "nameplate.jpg")
                if _need_update(nameplate_path, nameplate):
                    try:
                        if nameplate.endswith(".webm") or nameplate.endswith(".mp4"):
                            async with httpx.AsyncClient(timeout=15, verify=False) as dl:
                                r = await dl.get(nameplate, headers=HEADERS)
                            if r.status_code == 200:
                                tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                                tmp.write(r.content); tmp.close()
                                cap = cv2.VideoCapture(tmp.name)
                                if int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) > 0:
                                    ret, frame = cap.read()
                                    if ret: cv2.imwrite(nameplate_path, frame)
                                cap.release()
                                os.unlink(tmp.name)
                                _save_url(nameplate_path, nameplate)
                        else:
                            async with httpx.AsyncClient(timeout=10, verify=False) as dl:
                                r = await dl.get(nameplate, headers=HEADERS)
                            if r.status_code == 200:
                                Path(nameplate_path).write_bytes(r.content)
                                _save_url(nameplate_path, nameplate)
                    except Exception as e:
                        logger.warning(f"迷你背景缓存失败: {e}")

            # 头像
            avatar = str(cache_dir / "avatar.jpg")
            if avatar_url and _need_update(avatar, avatar_url):
                try:
                    async with httpx.AsyncClient(timeout=10, verify=False) as dl:
                        r = await dl.get(avatar_url, headers=HEADERS)
                    if r.status_code == 200:
                        Path(avatar).write_bytes(r.content)
                        _save_url(avatar, avatar_url)
                except Exception:
                    pass
            if not Path(avatar).exists():
                avatar = avatar_url

            # 头像框
            frame = str(cache_dir / "frame.png")
            if frame_url and _need_update(frame, frame_url):
                try:
                    async with httpx.AsyncClient(timeout=10, verify=False) as dl:
                        r = await dl.get(frame_url, headers=HEADERS)
                    if r.status_code == 200:
                        Path(frame).write_bytes(r.content)
                        _save_url(frame, frame_url)
                except Exception:
                    pass
            if not Path(frame).exists():
                frame = frame_url

            # 在线状态
            personastate = 0
            if 'persona in-game' in html:
                personastate = 1
            elif 'persona online' in html:
                personastate = 1

            # game_state
            rich_presence = ""
            m = re.search(r'<span class="game_state">(.+?)</span>', html, re.DOTALL)
            if m:
                rich_presence = m.group(1).strip()

            # 勋章: badge_icon img + name + xp
            badge_url = ""
            badge_name = ""
            badge_xp = ""
            bm = re.search(r'<img src="([^"]+)" class="badge_icon"', html, re.DOTALL)
            if bm: badge_url = bm.group(1).strip()
            bm2 = re.search(r'<div class="description">\s*<div class="name">(.+?)</div>', html, re.DOTALL)
            if bm2: badge_name = bm2.group(1).strip()
            bm3 = re.search(r'<div class="xp">(.+?)</div>', html, re.DOTALL)
            if bm3: badge_xp = bm3.group(1).strip()

            # 勋章图标缓存 (URL变更则重下)
            if badge_url:
                badge_path = str(cache_dir / "badge.png")
                if _need_update(badge_path, badge_url):
                    try:
                        async with httpx.AsyncClient(timeout=10, verify=False) as dl:
                            r = await dl.get(badge_url, headers=HEADERS)
                        if r.status_code == 200:
                            Path(badge_path).write_bytes(r.content)
                            _save_url(badge_path, badge_url)
                    except Exception:
                        pass
                if Path(badge_path).exists():
                    badge_url = badge_path

            return {
                "personaname": name,
                "game_name": game_name,
                "personastate": personastate,
                "rich_presence": rich_presence,
                "game_logo": game_logo,
                "avatar": avatar,
                "frame": frame,
                "nameplate": nameplate_path,
                "steam_level": steam_level,
                "badge_url": badge_url,
                "badge_name": badge_name,
                "badge_xp": badge_xp,
                "account_id": account_id,
            }

        except Exception as e:
            logger.debug(f"爬取 {url} 失败: {e}")
        return None

    # ==================== 推送 ====================

    async def _push_card(self, group_openid: str, player: dict, extra: dict | None = None, sid: str = "") -> None:
        """生成状态卡片"""
        if not group_openid or not self._api:
            return
        try:
            extra = extra or {}
            image_bytes = ImageService.generate_status_card(
                avatar_url=player.get("avatar", ""),
                frame_url=player.get("frame", ""),
                nameplate_url=player.get("nameplate", ""),
                player_name=player.get("personaname", ""),
                status=player.get("status_text", ""),
                game_name=player.get("game_name") or "",
                game_logo_url=player.get("game_logo", ""),
                rich_presence=player.get("rich_presence", ""),
                steam_level=str(player.get("steam_level", "")),
                played_minutes=extra.get("played_minutes", ""),
                badge_url=player.get("badge_url", ""),
                badge_name=player.get("badge_name", ""),
                badge_xp=player.get("badge_xp", ""),
            )

            # 本地保存: output/steam_cards/{32位id}.jpg
            _, account_id = convert_steam_id(sid) if sid else (sid, 0)
            local_dir = Path(__file__).resolve().parent.parent / "output" / "steam_cards"
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / f"{account_id}.jpg"
            with open(local_path, "wb") as f:
                f.write(image_bytes)
            logger.info(f"卡片已保存本地: {local_path}")

            # COS 上传: /steam_card/{32位id}.jpg
            cos = COSService.get_instance()
            cos_path = f"steam_card/{account_id}.jpg"
            url = cos.upload_image(image_bytes, filename=cos_path)
            if not url:
                await self._api.post_group_message(
                    group_openid=group_openid, msg_type=0,
                    content=f"{player.get('personaname','')} {player.get('status_text','')}",
                )
                return
            
            played_minutes_text = ""
            if extra.get("prev_game", ""):
                played_minutes_text = f'- **{extra.get("prev_game", "")}**共玩了**{extra.get("played_minutes", "")}**分钟'

            md_text = build_markdown(text=f"## Steam 视奸\n{played_minutes_text}\n![steam #246px #157px]({url})")
            await self._api.post_group_message(
                group_openid=group_openid, msg_type=2,
                markdown={"content": md_text}, content="",
            )
            logger.info(f"Steam 卡片推送成功: {player.get('personaname','')}")
        except Exception as e:
            logger.error(f"Steam 卡片推送失败: {e}")
