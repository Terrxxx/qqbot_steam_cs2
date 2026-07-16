"""QQ Bot Markdown 消息构建工具"""

from __future__ import annotations

def build_markdown(text: str, image_url: str = "", image_width: int = 320, image_height: int = 320) -> str:
    """构建 QQ 机器人 Markdown 消息内容

    Args:
        text: 文本正文
        image_url: 图片 URL（HTTP 可访问）
        image_width: 图片显示宽度（px）
        image_height: 图片显示高度（px）

    Returns:
        完整的 markdown 字符串
    """
    parts = [text.strip()]

    if image_url:
        # QQ markdown 图片: ![alt #widthpx #heightpx](url)
        parts.append("")
        parts.append(f"![image #{image_width}px]({image_url})")

    return "\n".join(parts)
