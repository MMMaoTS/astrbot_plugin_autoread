"""长期记忆桥接扩展点。

第一版为空实现（no-op）。后续版本可在此接入天使之魂或 LivingMemory。
"""

from astrbot.api import logger


class MemoryBridge:
    """将阅读笔记写入外部长期记忆后端的桥接器。

    backend 取值：
    - "none": 不写入外部记忆（默认）
    - "angel_memory": 写入天使之魂插件
    - "livingmemory": 写入 LivingMemory 插件
    """

    def __init__(self, backend: str = "none"):
        self.backend = backend

    async def write_memory(self, note: dict) -> None:
        """尝试将一条阅读笔记写入长期记忆。"""
        if self.backend == "none":
            return

        if self.backend == "angel_memory":
            logger.info("[AutoRead] angel_memory bridge not yet implemented, skipping")

        elif self.backend == "livingmemory":
            logger.info("[AutoRead] livingmemory bridge not yet implemented, skipping")

        else:
            logger.warning(f"[AutoRead] Unknown memory backend: {self.backend}")
