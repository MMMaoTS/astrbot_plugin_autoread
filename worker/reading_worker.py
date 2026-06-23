"""后台定时阅读 worker。

负责循环扫描到期阅读任务，调用 AutoReadService.read_next_chunk。
使用 ConfigService 读取动态配置（支持 WebUI 修改即时生效）。
"""

import asyncio
from datetime import datetime, timezone, timedelta

from astrbot.api import logger


class ReadingWorker:
    """后台定时调度器，不直接实现阅读逻辑。"""

    def __init__(self, *, context, config_service, service, state_store):
        self.context = context
        self.config_service = config_service
        self.service = service
        self.state_store = state_store

    async def run(self):
        """主循环，按 worker_tick_seconds 间隔扫描到期任务。"""
        tick = int(self.config_service.get("worker_tick_seconds", 60))
        logger.info(f"[AutoRead] Worker started, tick={tick}s")

        while True:
            try:
                # 每轮重新读取 tick（支持 WebUI 修改后即时生效）
                tick = int(self.config_service.get("worker_tick_seconds", 60))
                await self.tick()
                await asyncio.sleep(tick)
            except asyncio.CancelledError:
                logger.info("[AutoRead] Worker cancelled")
                raise
            except Exception:
                logger.exception("[AutoRead] Worker tick failed")
                await asyncio.sleep(tick)

    async def tick(self):
        """扫描一次到期任务。"""
        state = await self.state_store.load_state()
        sessions = state.get("sessions", {})
        now = self._now()

        for umo, session in sessions.items():
            if not session.get("enabled", False):
                continue
            if session.get("paused", False):
                continue
            if not session.get("current_book_id"):
                continue

            # 检查是否读完
            current_idx = session.get("current_chunk_index", 0)
            total = session.get("total_chunks", 0)
            if total > 0 and current_idx >= total:
                continue

            if not self._is_due(session.get("next_read_at"), now):
                continue

            logger.info(
                f"[AutoRead] Worker triggering read for session {umo}, "
                f"book={session.get('current_book_title')}, chunk={current_idx}/{total}"
            )

            try:
                await self.service.read_next_chunk(
                    umo=umo,
                    reason="定时阅读任务触发",
                    send_message=True,
                    source="worker",
                )
            except Exception:
                logger.exception(f"[AutoRead] Worker failed for session {umo}")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone(timedelta(hours=8)))

    @staticmethod
    def _is_due(next_read_at: str | None, now: datetime) -> bool:
        """next_read_at 为 None 或已过期时视为到期。"""
        if next_read_at is None:
            return True
        try:
            dt = datetime.fromisoformat(next_read_at)
            return now >= dt
        except (ValueError, TypeError):
            return True
