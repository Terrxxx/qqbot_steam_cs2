"""构建 QQ Bot 的交互式按钮消息"""

from __future__ import annotations
from botpy.types.inline import Action, Button, Keyboard, KeyboardRow, RenderData


def make_button(label: str, command: str, style: int = 1, action_type: int = 2, btn_id: str = "") -> Button:
    """创建一个按钮

    Args:
        label: 按钮显示文字
        command: 点击后发送的指令或回调数据（action_type=1时为回调标识，action_type=2时为指令）
        style: 按钮样式 0=灰色 1=蓝色主按钮
        action_type:
            0 = 跳转 URL 或小程序
            1 = 回调后台接口，触发 INTERACTION_CREATE，data 传给后台
            2 = 自动发送指令到输入框（默认）
        btn_id: 按钮唯一 ID（留空自动生成）
    """
    return Button(
        id=btn_id or f"btn_{label}",
        render_data=RenderData(
            label=label,
            visited_label=label,
            style=style,
        ),
        action=Action(
            type=action_type,
            permission={"type": 2},
            click_limit=10,
            data=command,
            at_bot_show_channel_list=False,
        ),
    )


def make_keyboard(buttons: list[Button], cols: int = 2) -> Keyboard:
    """将按钮列表排列成多行键盘"""
    rows: list[KeyboardRow] = []
    for i in range(0, len(buttons), cols):
        row_btns = buttons[i : i + cols]
        rows.append(KeyboardRow(buttons=row_btns))
    return {"content": Keyboard(rows=rows) }


def make_menu_keyboard() -> Keyboard:
    """默认的指令菜单键盘"""
    return make_keyboard([
        make_button("AI 生图", "/image ", style=1),
        make_button("Steam 视奸", "/steam", style=1),
        make_button("今日老婆", "/今日老婆", style=1),
        make_button("今日饰品", "/今日饰品", style=1),
        make_button("职业哥猜猜猜", "/职业哥猜猜猜", style=1),
        make_button("模拟开箱", "/模拟开箱", style=1),
    ], cols=2)
