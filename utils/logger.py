"""日志工具模块"""

import sys
from pathlib import Path
from loguru import logger

logger.remove()

logger.add(
    sys.stderr,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
    level="INFO",
    colorize=True,
)

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logger.add(
    LOGS_DIR / "bot_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    level="INFO",
    rotation="00:00",
    retention="7 days",
    encoding="utf-8",
    enqueue=True,
)

logger.add(
    LOGS_DIR / "error_{time:YYYY-MM-DD}.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    level="ERROR",
    rotation="00:00",
    retention="30 days",
    encoding="utf-8",
    enqueue=True,
)

__all__ = ["logger"]
