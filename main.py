import sys
import botpy
from config import config
from handlers.event_handler import QQBotClient
from utils.logger import logger


def main() -> None:
    """启动 QQ 机器人"""
    logger.info("=" * 40)
    logger.info("QQ Bot - CS2 机器人")
    logger.info(f"   AppID: {config.qqbot.app_id[:6]}***")
    logger.info(f"   环境: {'沙箱' if config.qqbot.is_sandbox else '正式'}")
    logger.info(f"   AI: {'已启用' if config.ai.enabled else '未启用'}")
    logger.info("=" * 40)

    intents = botpy.Intents(
        public_messages=True,
        direct_message=True,
        interaction=True,
    )

    client = QQBotClient(intents=intents)

    try:
        client.run(
            appid=config.qqbot.app_id,
            secret=config.qqbot.app_secret,
        )
    except KeyboardInterrupt:
        logger.info("机器人已停止")
    except Exception as e:
        logger.error(f"机器人运行异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
