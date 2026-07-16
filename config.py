import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
else:
    load_dotenv()

# ==================== 配置数据类 ====================

@dataclass
class QQBotConfig:
    app_id: str = field(default_factory=lambda: os.getenv("QQ_BOT_APP_ID", ""))
    app_secret: str = field(default_factory=lambda: os.getenv("QQ_BOT_APP_SECRET", ""))
    env: str = field(default_factory=lambda: os.getenv("QQ_BOT_ENV", "production"))

    @property
    def is_sandbox(self) -> bool:
        return self.env == "sandbox"

@dataclass
class AIConfig:
    api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))
    api_base: str = field(default_factory=lambda: os.getenv("AI_API_BASE", ""))
    model: str = field(default_factory=lambda: os.getenv("AI_MODEL", ""))
    img_api_key: str = field(default_factory=lambda: os.getenv("IMAGE_API_KEY", ""))
    img_api_base: str = field(default_factory=lambda: os.getenv("IMAGE_API_BASE", ""))
    img_model: str = field(default_factory=lambda: os.getenv("IMAGE_MODEL", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

@dataclass
class COSConfig:
    secret_id: str = field(default_factory=lambda: os.getenv("COS_SECRET_ID", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("COS_SECRET_KEY", ""))
    bucket: str = field(default_factory=lambda: os.getenv("COS_BUCKET", ""))
    region: str = field(default_factory=lambda: os.getenv("COS_REGION", "ap-guangzhou"))
    cdn_domain: str = field(default_factory=lambda: os.getenv("COS_CDN_DOMAIN", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.secret_id and self.secret_key and self.bucket)

    @property
    def base_url(self) -> str:
        if self.cdn_domain:
            return f"https://{self.cdn_domain}"
        return f"https://{self.bucket}.cos.{self.region}.myqcloud.com"

@dataclass
class LogConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

@dataclass
class SteamConfig:
    cookie: str = field(default_factory=lambda: os.getenv("STEAM_COOKIE", ""))
    poll_interval: int = field(default_factory=lambda: int(os.getenv("STEAM_POLL_INTERVAL", 60)))
    max_concurrent: int = field(default_factory=lambda: int(os.getenv("STEAM_MAX_CONCURRENT", 100)))

@dataclass
class AppConfig:
    qqbot: QQBotConfig = field(default_factory=QQBotConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    cos: COSConfig = field(default_factory=COSConfig)
    steam: SteamConfig = field(default_factory=SteamConfig)
    log: LogConfig = field(default_factory=LogConfig)

    @classmethod
    def load(cls) -> "AppConfig":
        config = cls()
        config._validate()
        return config

    def _validate(self) -> None:
        if not self.qqbot.app_id or not self.qqbot.app_secret:
            raise ValueError(
                "缺少 QQ Bot 配置！\n"
                "请复制 .env.example 为 .env，填入 QQ_BOT_APP_ID 和 QQ_BOT_APP_SECRET"
            )
        if self.qqbot.app_id == "":
            raise ValueError(
                "请到 QQ 开放平台 (https://q.qq.com) 注册机器人，"
                "获取 AppID 和 AppSecret 后填入 .env 文件"
            )

config = AppConfig.load()
