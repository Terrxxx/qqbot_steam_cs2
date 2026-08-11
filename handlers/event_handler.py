from __future__ import annotations

import re
import json
import random
import botpy

from pathlib import Path
from botpy.message import C2CMessage, GroupMessage
from handlers.command_handler import CommandRegistry, Reply
from services.scheduler_service import SchedulerService
from services.steam_service import SteamMonitor
from utils.logger import logger
from utils.markdown_utils import build_markdown
from utils.keyboard_utils import make_keyboard, make_button
from utils.random_utils import get as get_random

import botpy.message as _botpy_msg
import botpy.connection as _botpy_conn

class _PatchedGroupMessage(_botpy_msg.GroupMessage):
    def __init__(self, api, event_id, data):
        super().__init__(api, event_id, data)
        role = data.get("member_role", "") or data.get("role", "")
        if not role:
            author = data.get("author", {})
            role = author.get("member_role", "") or author.get("role", "")
        self.member_role = role or "member"

_botpy_msg.GroupMessage = _PatchedGroupMessage
_botpy_conn.GroupMessage = _PatchedGroupMessage
GroupMessage = _PatchedGroupMessage

USERS_CASE_DATA = {}

def get_wear_name(m):
    if(m<=0.07):
        return '崭新出厂'
    elif(m<=0.15):
        return '略有磨损'
    elif(m<=0.38):
        return '久经沙场'
    elif(m<=0.45):
        return '破损不堪'
    else:
        return '战痕累累'
		
COLOR_LIST = {
    1: "#B0C3D9",
    2: "#5E98D9",
    3: "#4B69FF",
    4: "#7E47FF",
    5: "#D32CE6",
    6: "#EB4B4B",
    7: "#FFD700",
}

COLOR_WEAR_LIST = {
    "崭新出厂": "#008000",
    "略有磨损": "#5CB85C",
    "久经沙场": "#F0AD4E",
    "破损不堪": "#D9534F",
    "战痕累累": "#993A38",
}

CASE_DATA = {}
CASE_KEY = []
skin_file = Path(__file__).resolve().parent.parent / "allSkin.json"
if skin_file.exists():
    with open(skin_file, "r", encoding="utf-8") as f:
        CASE_DATA = json.load(f)
        f.close()

    CASE_KEY = list(CASE_DATA.keys())


class QQBotClient(botpy.Client):
    """QQ 机器人客户端"""

    def __init__(self, intents: botpy.Intents):
        super().__init__(intents=intents)

    # ==================== 生命周期 ====================

    async def on_ready(self) -> None:
        logger.info(f"机器人 {self.robot.name} 已上线！")
        SchedulerService.start()

        monitor = SteamMonitor.get_instance()
        monitor.set_api(self.api)
        monitor.start()

        logger.info("所有服务已就绪")

    # ==================== 权限检查 ====================

    @staticmethod
    def is_group_admin(member_role: str) -> bool:
        """member_role 取值: member / admin / owner"""
        return member_role in ("admin", "owner")

    # ==================== 消息事件 ====================

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        """群聊 @机器人 消息"""
        content = self._clean_at(message.content)
        group_id = getattr(message, "group_openid", "")
        member_id = message.author.member_openid
        member_role = getattr(message, "member_role", "member")
        logger.info(f"[群聊@] 群={group_id} 用户={member_id} 权限={member_role} 内容={content[:80]}")

        reply = await CommandRegistry.handle(
            content,
            group_openid=group_id,
            user_id=member_id,
            member_openid=member_id,
            member_role=member_role,
        )
        await self._do_reply(message, reply)

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        """私聊消息"""
        content = message.content.strip()
        logger.info(f"[私聊] 用户={message.author.user_openid} 内容={content[:80]}")
        reply = await CommandRegistry.handle(content, user_id=message.author.user_openid)
        await self._do_reply(message, reply)

    # ==================== 按钮回调 ====================

    async def on_interaction_create(self, interaction) -> None:
        """用户点击回调按钮 (action_type=1)

          - id: 交互ID（用于回调响应）
          - type: InteractionType (11=INLINE_KEYBOARD)
          - data.type: InteractionDataType (11=按钮点击)
          - data.resolved: 按钮 data 内容
          - group_openid / user_openid: 来源群/用户
        """
        # logger.info(
        #     f"[按钮回调] id={interaction.id} "
        #     f"type={interaction.type} "
        #     f"data_type={interaction.data.type if interaction.data else 'N/A'} "
        #     f"group={getattr(interaction, 'group_openid', '')} "
        #     f"user={getattr(interaction, 'user_openid', '')}"
        # )

        btn_data = ""
        if interaction.data and hasattr(interaction.data, "resolved"):
            btn_data = str(interaction.data.resolved) if interaction.data.resolved else ""

        logger.info(f"按钮data: {btn_data}")

        def get_case(data, is_group=False):
            button_data = getattr(data, "button_data", "").split(":")
            func = button_data[0]

            if func in CASE_KEY:
                case_type = button_data[0]
                page = 0
                if len(button_data) > 1:
                    page = int(button_data[1])
                total_count = len(CASE_DATA[case_type])
                max_page = total_count // 8 + (1 if total_count % 8 else 0)
                if page < 0 or page >= max_page: return None
                if total_count > 8:
                    return Reply(
                        text=f"## 模拟开箱\n请选择你要开启的箱子 {page+1}/{max_page}",
                        keyboard=make_keyboard(
                            [
                            make_button(
                                "⬅️上一页", f"{case_type}:{page-1}", 1, 1
                            ),
                            make_button(
                                "下一页➡️", f"{case_type}:{page+1}", 1, 1
                            )
                        ]+[
                            make_button(
                                i['title'], f"open:{case_type}:{i['id']}:{i['type']}:{i['title']}", 1, 1
                            ) for i in CASE_DATA[case_type][0+8*(page):8*(page+1)]
                        ])
                    )
                else:
                    return Reply(
                        text="## 模拟开箱\n请选择你要开启的箱子",
                        keyboard=make_keyboard([
                            make_button(
                                i['title'], f"open:{case_type}:{i['id']}:{i['type']}:{i['title']}", 1, 1
                            ) for i in CASE_DATA[case_type]
                        ])
                    )
                
            elif func == "open":
                openid = getattr(interaction, 'user_openid', '')
                open_user_text = "<qqbot-at-user id='{}' />".format(getattr(interaction, 'group_member_openid', '')) if is_group else "你"
                if is_group:
                    if USERS_CASE_DATA.get(openid) is None:
                        USERS_CASE_DATA[openid] = 0
                    USERS_CASE_DATA[openid] += 1
                    if USERS_CASE_DATA.get(openid) > 3:
                        if USERS_CASE_DATA[openid] >= 4:
                            return self.api.on_interaction_result(interaction.id, code=0)
                        return Reply(
                            text=(
                                f"## 模拟开箱\n"
                                f"{open_user_text}\n"
                                f"进入模拟开箱已达上限，若还想继续开箱，可私聊开箱"
                            ),
                            keyboard=make_keyboard([])
                        )
                case_type = button_data[1]
                case_id = int(button_data[2])
                CASE = list(filter(lambda x: x['id']==case_id, CASE_DATA[case_type]))[0]
                get_type = get_random(int(button_data[3]))
                ITEM = random.choice(list(filter(lambda x: x['type']==get_type, CASE['data'])))
                wear = random.uniform(ITEM['wear'][0], ITEM['wear'][1])
                color = COLOR_LIST[get_type]
                wear_name = f" ({get_wear_name(wear)})"
                wear_color = COLOR_WEAR_LIST[get_wear_name(wear)]
                if case_type in ['case', 'case2', 'mapcase']:
                    gold_star_text = f"{'（★）' if get_type == 7 else ''}"
                    wear_name = f" ({get_wear_name(wear)})"
                    wear_color = COLOR_WEAR_LIST[get_wear_name(wear)]
                    wear_text = f"磨损: <font color='{wear_color}'>{wear}</font>\n模板: {random.randint(0, 999)}"
                else:
                    gold_star_text = ""
                    wear_text = ""
                    wear_name = ""
                return Reply(
                    text=(
                        f"## 模拟开箱\n"
                        f"开启人: {open_user_text}\n"
                        f"武器箱: {button_data[4]}\n"
                        f"![img#150px](https://g.fp.ps.netease.com/market/file/{ITEM['src']}.png)\n"
                        f"<font color='{color}'>{ITEM['gun']}{gold_star_text} | {ITEM['name']}</font>{wear_name}\n"
                        f"{wear_text}"
                        # f'<div style="width: 100%;margin-top: -3px;position: absolute;padding-bottom: 4px;"><div style="margin-left: -6px;position: absolute;height: 0;border-style: solid;border-width: 5px 5px 0 5px;border-color: #fff transparent transparent transparent;left: 35.64724326133728%"></div></div><div style="width: 100%;background-color: #f5f5f5;height: 5px;"><div style="background-color: green;float: left;height: 100%;width: 7%;"></div><div style="background-color: #5cb85c;float: left;height: 100%;" style="width: 8%;"></div><div style="background-color: #f0ad4e;float: left;height: 100%;width: 23%;"></div><div style="background-color: #d9534f;float: left;height: 100%;width: 7%;"></div><div style="background-color: #993A38;float: left;height: 100%;width: 55%;"></div></div>'
                    ),
                    keyboard=make_keyboard([
                            make_button(
                                "再开一次", f"open:{case_type}:{case_id}:{button_data[3]}:{button_data[4]}", 1, 1
                            ),
                            make_button(
                                "试试别的", f"{case_type}:{0}", 1, 1
                            )
                        ])
                )

        group_id = getattr(interaction, "group_openid", "")
        user_id = getattr(interaction, "user_openid", "")
        if group_id:
            rep = get_case(interaction.data.resolved, True)
            if not rep: return
            try:
                await self.api.post_group_message(
                    group_openid=group_id,
                    msg_type=2,
                    markdown={
                        "markdown": {"content": rep.text},
                        "content": rep.text,
                    },
                    keyboard=rep.keyboard
                )
            except Exception as e:
                logger.error(f"按钮回调群聊回复失败: {e}")
        elif user_id:
            rep = get_case(interaction.data.resolved)
            if not rep: return
            try:
                await self.api.post_c2c_message(
                    openid=user_id,
                    msg_type=2,
                    markdown={
                        "markdown": {"content": rep.text},
                        "content": rep.text,
                    },
                    keyboard=rep.keyboard
                )
            except Exception as e:
                logger.error(f"按钮回调私聊回复失败: {e}")

        # 必须响应交互，否则客户端超时
        await self.api.on_interaction_result(interaction.id, code=0)

    # ==================== 内部 ====================

    @staticmethod
    def _clean_at(content: str) -> str:
        return re.sub(r"<@!\d+>", "", content).strip()

    async def _do_reply(self, message: C2CMessage | GroupMessage, reply: Reply) -> None:
        """统一的消息回复"""
        has_image = bool(reply.image_url)

        if has_image:
            md_text = build_markdown(text=reply.text, image_url=reply.image_url)
            kwargs: dict = {
                "msg_type": 2,
                "markdown": {"content": md_text},
                "content": reply.text,
            }
        else:
            md_text = build_markdown(text=reply.text)
            kwargs: dict = {
                "msg_type": 2,
                "markdown": {"content": md_text},
                "content": reply.text,
            }

        if reply.keyboard:
            kwargs["keyboard"] = reply.keyboard

        try:
            await message.reply(**kwargs)
            logger.info(f"回复成功")
        except Exception as e:
            try:
                await message.reply(msg_type=0, content=reply.text)
                logger.warning(f"降级纯文本成功: {e}")
            except Exception as e2:
                logger.error(f"回复彻底失败: {e2}")
