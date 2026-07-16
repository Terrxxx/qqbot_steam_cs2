from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.logger import logger

# 回调类型：当定时任务触发时调用此函数发送消息
_send_callback: Optional[Callable] = None

class SchedulerService:
    """定时任务服务"""

    _scheduler: Optional[AsyncIOScheduler] = None
    _job_counter: int = 0

    @classmethod
    def get_scheduler(cls) -> AsyncIOScheduler:
        """获取或创建调度器实例"""
        if cls._scheduler is None:
            cls._scheduler = AsyncIOScheduler()
            logger.info("APScheduler 已初始化")
        return cls._scheduler

    @classmethod
    def start(cls) -> None:
        """启动调度器"""
        scheduler = cls.get_scheduler()
        if not scheduler.running:
            scheduler.start()
            logger.info("定时任务调度器已开始运行")

    @classmethod
    def shutdown(cls) -> None:
        """关闭调度器"""
        if cls._scheduler and cls._scheduler.running:
            cls._scheduler.shutdown(wait=False)
            logger.info("定时任务调度器已停止")

    @classmethod
    def add_delayed_reminder(cls, seconds: int, message: str, target_type: str = "c2c", target_id: str = "") -> str:
        """添加一次性延迟提醒

        Args:
            seconds: 延迟秒数
            message: 提醒消息内容
            target_type: 目标类型（c2c/group）
            target_id: 目标 ID（用户 openid 或群 openid）

        Returns:
            任务 ID
        """
        scheduler = cls.get_scheduler()
        run_time = datetime.now() + timedelta(seconds=seconds)
        cls._job_counter += 1
        job_id = f"remind_{cls._job_counter}"

        async def _job():
            logger.info(f"提醒触发: {message[:50]}")
            if _send_callback:
                await _send_callback(target_type, target_id, f"**提醒**\n{message}")

        scheduler.add_job(
            _job,
            trigger="date",
            run_date=run_time,
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"已添加延迟提醒: job_id={job_id} delay={seconds}s")
        return job_id

    @classmethod
    def remove_job(cls, job_id: str) -> bool:
        """移除指定任务"""
        scheduler = cls.get_scheduler()
        try:
            scheduler.remove_job(job_id)
            logger.info(f"已移除任务: {job_id}")
            return True
        except Exception as e:
            logger.warning(f"移除任务失败: {e}")
            return False

    @classmethod
    def list_jobs(cls) -> list[dict]:
        """列出所有待执行任务"""
        scheduler = cls.get_scheduler()
        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "next_run": str(job.next_run_time),
                "trigger": str(job.trigger),
            })
        return jobs
