from __future__ import annotations

import asyncio
import inspect
import time
import json
import httpx
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from services.database import Database
from services.steam_service import SteamMonitor
from services.ai_service import AIService
from services.image_service import ImageService
from utils.logger import logger
from utils.keyboard_utils import make_menu_keyboard, make_keyboard, make_button
from services.steam_service import convert_steam_id
from services.cos_service import COSService

if TYPE_CHECKING:
    from botpy.types.inline import Keyboard

CmdHandler = Callable[..., Any]

# 触发图片生成的关键词
IMAGE_KEYWORDS = [
    "生成图片", "生成一张", "画一张", "画一个", "画个", "生成一张图",
    "画图", "生成图", "做一张图", "来张图", "来一张",
    "generate image", "draw", "create image",
    "帮我画", "帮我生成", "给我画", "给我生成",
    "图片生成", "生成图像", "绘制", "生图", "Generate"
]

CASE_DATA = {}
CASE_KEY = []
skin_file = Path(__file__).resolve().parent.parent / "allSkin.json"
if skin_file.exists():
    with open(skin_file, "r", encoding="utf-8") as f:
        CASE_DATA = json.load(f)
        f.close()

    CASE_KEY = list(CASE_DATA.keys())

@dataclass
class Reply:
    text: str = ""
    image_url: str | None = None
    keyboard: "Keyboard | None" = None


@dataclass
class Command:
    name: str
    help_text: str
    usage: str
    handler: CmdHandler
    admin_only: bool = False


class CommandRegistry:
    _commands: dict[str, Command] = {}
    _ai_service: Optional[AIService] = None
    _ctx_group: str = ""
    _ctx_user: str = ""

    @classmethod
    def register(cls, name: str, help_text: str, usage: str = "", admin_only: bool = False):
        def decorator(func: CmdHandler):
            cls._commands[name] = Command(
                name=name, help_text=help_text,
                usage=usage or name, handler=func, admin_only=admin_only,
            )
            return func
        return decorator

    @classmethod
    def get_ai_service(cls) -> AIService:
        if cls._ai_service is None:
            cls._ai_service = AIService()
        return cls._ai_service

    @classmethod
    def get_help_text(cls) -> str:
        lines = ["## 可用命令\n"]
        for cmd in cls._commands.values():
            lines.append(f"- `{cmd.name}` — {cmd.help_text}")
            # lines.append(f"  用法: {cmd.usage}")
        return "\n".join(lines)

    @classmethod
    async def _run_handler(cls, handler: CmdHandler, args: str) -> Any:
        if inspect.iscoroutinefunction(handler):
            return await handler(args)
        return await asyncio.to_thread(handler, args)

    @classmethod
    def _is_image_request(cls, content: str) -> bool:
        """检测消息是否为图片生成请求"""
        return any(kw in content for kw in IMAGE_KEYWORDS)

    @classmethod
    async def handle(cls, content: str, group_openid: str = "", user_id: str = "") -> Reply:
        cls._ctx_group = group_openid
        cls._ctx_user = user_id

        content = content.strip()
        logger.debug(f"路由: {content[:80]}")

        if not content:
            return Reply(
                text="发送指令或点击下方按钮 ⬇️",
                keyboard=make_menu_keyboard(),
            )

        # 匹配注册的命令
        sorted_cmds = sorted(cls._commands.items(), key=lambda x: len(x[0]), reverse=True)
        for cmd_name, command in sorted_cmds:
            if content.startswith(cmd_name):
                args = content[len(cmd_name):].strip()
                logger.info(f"匹配: {cmd_name} args={args[:50]}")
                try:
                    raw = await cls._run_handler(command.handler, args)
                    result = raw if isinstance(raw, Reply) else Reply(text=str(raw))
                    if cmd_name == "/help" and not result.keyboard:
                        result.keyboard = make_menu_keyboard()
                    return result
                except Exception as e:
                    logger.error(f"命令异常 {cmd_name}: {e}")
                    return Reply(text=f"命令执行出错: {e}")

        # 图片生成请求
        if cls._is_image_request(content):
            logger.info(f"检测到图片请求: {content[:50]}")
            try:
                image_url = await ImageService.generate_and_upload(prompt=content)
                if image_url:
                    return Reply(text=f"{content[:40]}", image_url=image_url)
                return Reply(text="图片生成/上传失败")
            except Exception as e:
                logger.error(f"图片生成异常: {e}")
                return Reply(text=f"图片生成异常: {e}")

        # AI 对话
        ai = cls.get_ai_service()
        if ai.enabled:
            try:
                reply = await ai.chat_with_search(content)
                return Reply(text=reply)
            except Exception as e:
                logger.error(f"AI 对话异常: {e}")
                return Reply(text=f"AI 请求失败: {e}")
        return Reply(text="发送 `/help` 查看可用命令")


# ==================== 基础命令 ====================

@CommandRegistry.register(name="/help", help_text="显示帮助信息", usage="/help")
def cmd_help(_: str) -> str:
    return CommandRegistry.get_help_text()


@CommandRegistry.register(name="/clear", help_text="清除 AI 对话上下文", usage="/clear")
def cmd_clear(_: str) -> str:
    CommandRegistry.get_ai_service().clear_history()
    return "对话上下文已清除"

# ==================== 每日随机 ====================

async def _daily_random(cache_type: str, api_url: str, uid: str) -> str:
    today = time.strftime("%Y-%m-%d")
    db = Database()
    cached = db.get_daily_cache(uid, today, cache_type)
    if cached:
        logger.info(f"命中缓存: {cache_type} user={uid}")
        return json.loads(cached)
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as c:
            resp = await c.get(api_url)
        if resp.status_code != 200:
            return "API 请求失败，请稍后重试"
        data = resp.json()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        db.set_daily_cache(uid, today, cache_type, text)
        logger.info(f"已缓存: {cache_type} user={uid}")
        return json.loads(text)
    except Exception as e:
        logger.error(f"{cache_type} 请求失败: {e}")
        return f"请求失败: {e}"

@CommandRegistry.register(name="/今日老婆", help_text="今日老婆", usage="/今日老婆")
async def cmd_daily_pro(_: str) -> str:
    data = await _daily_random("pro", "https://yrxs.net/counter-strike/random_pro", CommandRegistry._ctx_user or "unknown")
    if not data:
        return Reply(text="你的老婆被Niko抓走了!")
    return Reply(text=(
        f"你的老婆是**{data['player']['team']}**战队的「**{data['player']['name']}**」\n"
        f"[点击查看老婆照片]({data['avatar']})"  # hltv 有防盗链 暂时先不直接显示
    ))

@CommandRegistry.register(name="/今日饰品", help_text="今日饰品", usage="/今日饰品")
async def cmd_daily_skin(_: str) -> str:
    data = await _daily_random("skin", "https://yrxs.net/kx/random_skin", CommandRegistry._ctx_user or "unknown")
    if not data:
        return Reply(text="你的今日饰品被Niko抓走了!")
    return Reply(text=(
        f"你的今日饰品是**{data['case']['title']}**的**{data['skin']['gun']} | {data['skin']['name']}**\n"
        f"![img #200px](https://g.fp.ps.netease.com/market/file/{data['skin']['src']})"
    ))

# ==================== 职业哥猜猜猜 ====================

@CommandRegistry.register(name="/职业哥猜猜猜", help_text="职业哥猜猜猜", usage="/职业哥猜猜猜")
async def cmd_daily_skin(_: str) -> str:
    return Reply(text=(
        f"[职业哥猜猜猜](https://yrxs.net/counter-strike/daily)"
    ))

# ==================== 图片生成 ====================

@CommandRegistry.register(name="/image", help_text="AI 生成图片", usage="/image <描述>")
async def cmd_image(args: str) -> Reply:
    if not args:
        return Reply(text="用法: `/image <描述>`\n例如: `/image Niko捧起major奖杯`")
    try:
        start_time = time.time()
        image_url = await ImageService.generate_and_upload(prompt=args)
        if image_url:
            return Reply(text=(
                f"{args}\n"
                f"花费时间: {int(time.time() - start_time)}s\n"
                f"![img #200px]({image_url})"
            ))
        return Reply(text="图片生成/上传失败")
    except Exception as e:
        logger.error(f"图片生成异常: {e}")
        return Reply(text=f"图片生成异常: {e}")

# ==================== 其他 ====================

# @CommandRegistry.register(name="/opencase", help_text="模拟开箱", usage="/opencase <箱子名>")
@CommandRegistry.register(name="/oc", help_text="模拟开箱", usage="/oc <箱子名>")
def cmd_opencase(args: str) -> str:
    """模拟 CS2 开箱，从 addSkin.json 读取皮肤数据"""
    if not CASE_DATA:
        return "数据读取错误"

    return Reply(text=
        " ## 模拟开箱\n"
        "请选择你要开启的箱子类型",
        keyboard=make_keyboard([
            make_button(
                "武器箱", "case", 1, 1
            ),
            make_button(
                "特殊收藏品", "case2", 1, 1
            ),
            make_button(
                "地图收藏包", "mapcase", 1, 1
            ),
            make_button(
                "印花胶囊", "stick", 1, 1
            ),
            make_button(
                "Major胶囊", "majorstick", 1, 1
            ),
            make_button(
                "胸章胶囊", "badge", 1, 1
            ),
            make_button(
                "布章包", "patch", 1, 1
            ),
            make_button(
                "音乐盒", "musickit", 1, 1
            ),
            make_button(
                "涂鸦箱", "graffiti", 1, 1
            ),
            make_button(
                "挂件", "charm", 1, 1
            )
        ])
    )

# ==================== Steam 视奸 ====================

@CommandRegistry.register(name="/steam", help_text="Steam 游戏视奸", usage="/steam add/remove/list/check")
async def cmd_steam(args: str) -> str:
    ctx = CommandRegistry._ctx_group
    sub = args.split(maxsplit=1)
    action = sub[0].lower() if sub else ""
    rest = sub[1].strip() if len(sub) > 1 else ""

    if action == "add":
        if not rest:
            return "用法: `/steam add <steam_id>`\n例如: `/steam add 7656XXXXXXXXXXXX`"
        if not ctx:
            return "请在群聊中使用此命令"
        raw_id = rest.split()[0].strip()
        steam64, _ = convert_steam_id(raw_id)
        monitor = SteamMonitor.get_instance()
        player = await monitor.check_one(steam64)
        if player is None:
            return f"无法获取 Steam 资料，请确认 {raw_id} 正确且资料公开或重试"
        display_name = player.get("personaname", steam64)
        db = Database()
        ok = db.add_profile(steam64, display_name, ctx)
        if ok:
            return f"已添加 Steam 视奸: {display_name} ({steam64})"
        return "添加失败"

    elif action == "remove":
        if not rest:
            return "用法: `/steam remove <steam_id>`"
        db = Database()
        ok = db.remove_profile(rest.strip(), ctx or "")
        return f"已移除: {rest.strip()}" if ok else "移除失败"

    elif action == "list":
        db = Database()
        profiles = db.get_active_profiles(ctx or "")
        if not profiles:
            return "当前群没有视奸中的 Steam 账号"
        lines = ["### Steam 视奸列表\n"]
        for p in profiles:
            sn = p["steam_name"] or "?"
            dn = p["display_name"]
            sid = p["steam_id"]
            lines.append(f"- **{dn}** — {sid}")
        return "\n".join(lines)

    elif action == "check":
        if not rest:
            return "用法: `/steam check <steam_id>`"

        raw_id = rest.strip()
        steam64, account_id = convert_steam_id(raw_id)
        monitor = SteamMonitor.get_instance()
        result = await monitor.check_one(steam64)
        if result is None:
            return f"查询失败 `{raw_id}`, 请检查账户**游戏详情**隐私是否设置为**公开**"

        game = result.get("game_name")
        result["status_text"] = "游戏中" if game else {0: "离线", 1: "在线", 2: "离开"}.get(result.get("personastate", 0), "离线")

        # 生成卡片图片
        image_bytes = ImageService.generate_status_card(
            avatar_url=result.get("avatar", ""),
            frame_url=result.get("frame", ""),
            nameplate_url=result.get("nameplate", ""),
            player_name=result.get("personaname", ""),
            status=result["status_text"],
            game_name=game or "",
            game_logo_url=result.get("game_logo", ""),
            rich_presence=result.get("rich_presence", ""),
            steam_level=str(result.get("steam_level", "")),
            badge_url=result.get("badge_url", ""),
            badge_name=result.get("badge_name", ""),
            badge_xp=result.get("badge_xp", ""),
        )

        # 本地保存
        local_dir = Path(__file__).resolve().parent.parent / "output" / "steam_cards"
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / f"{account_id}.jpg").write_bytes(image_bytes)

        # COS 上传
        cos = COSService.get_instance()
        url = cos.upload_image(image_bytes, filename=f"steam_card/{account_id}.jpg")
        if url:
            return Reply(text=f"### Steam 视奸\n![steam #246px #157px]({url})")
        return f"卡片上传失败"

    else:
        return Reply(text=(
            "### Steam 视奸\n"
            "用法:\n"
            "- `/steam add <steam_id>` — 添加视奸\n"
            "- `/steam remove <steam_id>` — 移除视奸\n"
            "- `/steam list` — 查看视奸列表\n"
            "- `/steam check <steam_id>` — 手动查询状态"),
            keyboard=make_keyboard([
                make_button("新增", "/steam add ", 1, 2),
                make_button("移除", "/steam remove ", 1, 2),
                make_button("列表", "/steam list", 1, 2),
                make_button("查询", "/steam check ", 1, 2),
            ])
        )

