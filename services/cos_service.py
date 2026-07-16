from __future__ import annotations

import uuid
import time
from datetime import datetime
from io import BytesIO
from typing import Optional
from config import config
from utils.logger import logger
from qcloud_cos import CosConfig, CosS3Client


class COSService:
    """腾讯云 COS 对象存储上传服务（单例）"""

    _instance: Optional["COSService"] = None
    _client = None

    def __new__(cls) -> "COSService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._enabled = config.cos.enabled

        if not self._enabled:
            logger.info("COS 服务未启用（缺少凭证）")
            return

        try:
            cos_config = CosConfig(
                Region=config.cos.region,
                SecretId=config.cos.secret_id,
                SecretKey=config.cos.secret_key,
                Scheme="https",
            )
            self._client = CosS3Client(cos_config)
            logger.info(
                f"COS 服务已初始化: bucket={config.cos.bucket} "
                f"region={config.cos.region}"
            )
        except ImportError:
            self._enabled = False
            logger.warning("cos-python-sdk-v5 未安装，请执行: pip install cos-python-sdk-v5")
        except Exception as e:
            self._enabled = False
            logger.error(f"COS 客户端初始化失败: {e}")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    @classmethod
    def get_instance(cls) -> "COSService":
        return cls()

    def upload_image(self, image_bytes: bytes, filename: str = "", content_type: str = "image/jpeg") -> Optional[str]:
        """上传图片到 COS 并返回公网 URL

        Args:
            image_bytes: 图片二进制数据
            filename: 文件名（留空自动生成）
            content_type: MIME 类型

        Returns:
            URL | None
        """
        if not self.enabled:
            logger.warning("COS 未启用，无法上传")
            return None

        if not filename:
            date_str = datetime.now().strftime("%Y%m%d")
            short_id = uuid.uuid4().hex[:8]
            filename = f"images/gpt-image/{date_str}/{short_id}.jpg"

        try:
            # put_object 同步上传（在 async context 中调用，不会阻塞太久）
            response = self._client.put_object(
                Bucket=config.cos.bucket,
                Key=filename,
                Body=BytesIO(image_bytes),
                ContentType=content_type,
            )

            url = f"{config.cos.base_url}/{filename}?t={int(time.time())}"
            logger.info(f"图片已上传 COS: {url}")
            return url

        except Exception as e:
            logger.error(f"COS 上传失败: {e}")
            return None
