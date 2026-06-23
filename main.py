import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .services.book_loader import BookLoader
from .services.text_chunker import TextChunker
from .services.reading_state import ReadingStateStore
from .services.note_writer import NoteWriter
from .services.memory_bridge import MemoryBridge
from .services.autoread_service import AutoReadService
from .worker.reading_worker import ReadingWorker

PLUGIN_NAME = "astrbot_plugin_autoread"

# 兼容低版本 AstrBot 的 data path fallback
try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:
    def get_astrbot_data_path() -> str:
        return str(Path.cwd() / "data")


@register(
    "astrbot_plugin_autoread",
    "MMMaoTS",
    "让虚拟角色持续阅读本地文本、记录进度、生成阶段性读书笔记，并支持自然对话工具调用与后台主动分享。",
    "0.1.0",
)
class AutoReadPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else AstrBotConfig({})

        # 运行数据目录
        plugin_name = getattr(self, "name", None) or PLUGIN_NAME
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "books").mkdir(exist_ok=True)
        (self.data_dir / "chunks").mkdir(exist_ok=True)
        (self.data_dir / "notes").mkdir(exist_ok=True)

        # 基础设施层
        self.state_store = ReadingStateStore(self.data_dir)
        self.book_loader = BookLoader(
            data_dir=self.data_dir,
            allowed_extensions=list(self.config.get("allowed_extensions", [".txt", ".md"])),
        )
        self.chunker = TextChunker(
            chunk_size=int(self.config.get("chunk_size", 1800)),
            chunk_overlap=int(self.config.get("chunk_overlap", 120)),
        )
        self.note_writer = NoteWriter(
            context=self.context,
            config=self.config,
        )
        self.memory_bridge = MemoryBridge(
            backend=self.config.get("memory_backend", "none"),
        )

        # 业务编排层
        self.autoread_service = AutoReadService(
            context=self.context,
            config=self.config,
            data_dir=self.data_dir,
            state_store=self.state_store,
            book_loader=self.book_loader,
            chunker=self.chunker,
            note_writer=self.note_writer,
            memory_bridge=self.memory_bridge,
        )

        # 后台 worker
        self.worker = ReadingWorker(
            context=self.context,
            config=self.config,
            service=self.autoread_service,
            state_store=self.state_store,
        )
        self._worker_task: asyncio.Task | None = None

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def initialize(self):
        """插件初始化时启动后台 worker。"""
        if self.config.get("enabled", True):
            self._worker_task = asyncio.create_task(self.worker.run())
            logger.info("[AutoRead] Plugin initialized, worker started")

    async def terminate(self):
        """插件卸载/热重载时取消 worker。"""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
            logger.info("[AutoRead] Worker terminated")

    # ==================================================================
    # /read 命令组
    # ==================================================================

    @filter.command_group("read")
    def read(self):
        pass

    @read.command("ping")
    async def read_ping(self, event: AstrMessageEvent):
        """检查插件是否正常加载。"""
        yield event.plain_result("AutoRead 插件已加载。")

    @read.command("bind")
    async def read_bind(self, event: AstrMessageEvent):
        """绑定当前会话，用于后续主动分享。"""
        umo = event.unified_msg_origin
        result = await self.autoread_service.bind(umo)
        yield event.plain_result(result)

    @read.command("import")
    async def read_import(self, event: AstrMessageEvent, filename: str = ""):
        """导入本地书籍。

        Args:
            filename(string): 要导入的文件名（文件须位于 plugin_data/astrbot_plugin_autoread/books/ 下）

        Examples:
            /read import mybook.txt
        """
        if not filename:
            yield event.plain_result(
                "请指定文件名。\n"
                "用法: /read import <文件名>\n"
                "文件须放在 plugin_data/astrbot_plugin_autoread/books/ 目录下。"
            )
            return
        result = await self.autoread_service.import_book(filename)
        yield event.plain_result(result)

    @read.command("list")
    async def read_list(self, event: AstrMessageEvent):
        """列出已导入的书籍。"""
        result = await self.autoread_service.list_books()
        yield event.plain_result(result)

    @read.command("choose")
    async def read_choose(self, event: AstrMessageEvent, preference: str = ""):
        """根据偏好选择书籍。

        Args:
            preference(string): 阅读偏好（可选）
        """
        umo = event.unified_msg_origin
        result = await self.autoread_service.choose_book(umo=umo, preference=preference)
        yield event.plain_result(result)

    @read.command("start")
    async def read_start(self, event: AstrMessageEvent, book_id: str = ""):
        """开始持续阅读一本书。

        Args:
            book_id(string): 要阅读的书籍 ID

        Examples:
            /read start book_20260623_001
        """
        if not book_id:
            yield event.plain_result(
                "请指定 book_id。\n"
                "用法: /read start <book_id>\n"
                "使用 /read list 查看可用书籍。"
            )
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.start_book(umo=umo, book_id=book_id)
        yield event.plain_result(result)

    @read.command("step")
    async def read_step(self, event: AstrMessageEvent):
        """阅读下一段文本并生成笔记。"""
        umo = event.unified_msg_origin
        result = await self.autoread_service.read_next_chunk(
            umo=umo,
            reason="/read step 手动触发",
            send_message=False,
            source="command",
        )
        yield event.plain_result(result)

    @read.command("status")
    async def read_status(self, event: AstrMessageEvent):
        """查看当前阅读进度。"""
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_status(umo)
        yield event.plain_result(result)

    @read.command("notes")
    async def read_notes(self, event: AstrMessageEvent, limit: str = "5"):
        """查看最近阅读笔记。

        Args:
            limit(string): 返回条数，默认 5
        """
        try:
            n = int(limit)
        except ValueError:
            n = 5
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_notes(umo=umo, limit=n)
        yield event.plain_result(result)

    @read.command("pause")
    async def read_pause(self, event: AstrMessageEvent):
        """暂停后台阅读。"""
        result = await self.autoread_service.pause(event.unified_msg_origin)
        yield event.plain_result(result)

    @read.command("resume")
    async def read_resume(self, event: AstrMessageEvent):
        """恢复后台阅读。"""
        result = await self.autoread_service.resume(event.unified_msg_origin)
        yield event.plain_result(result)

    @read.command("stop")
    async def read_stop(self, event: AstrMessageEvent):
        """停止当前阅读任务。"""
        result = await self.autoread_service.stop(event.unified_msg_origin)
        yield event.plain_result(result)

    # ==================================================================
    # LLM Tool 入口 —— 自然对话触发
    # ==================================================================

    @filter.llm_tool(name="autoread_list_books")
    async def autoread_list_books(self, event: AstrMessageEvent):
        """列出当前可供持续阅读的书籍。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        result = await self.autoread_service.list_books()
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_choose_book")
    async def autoread_choose_book(self, event: AstrMessageEvent, preference: str = ""):
        """根据当前角色兴趣和用户给出的偏好，从已导入书籍中选择一本想读的书。该工具只选择书，不会自动开始阅读。

        Args:
            preference(string): 用户或角色表达的阅读偏好，例如"童话""科幻""哲学""轻松一点""你自己感兴趣的"。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.choose_book(umo=umo, preference=preference)
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_start_book")
    async def autoread_start_book(
        self, event: AstrMessageEvent, book_id: str, interval_minutes: float = 1440
    ):
        """开始持续阅读一本已导入的书，并绑定当前会话用于后续主动分享。

        Args:
            book_id(string): 要开始阅读的书籍 ID，必须来自 autoread_list_books 或 autoread_choose_book 的结果。
            interval_minutes(number): 阅读间隔，单位分钟。默认 1440 分钟，即每天一次。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.start_book(
            umo=umo,
            book_id=book_id,
            interval_minutes=interval_minutes,
        )
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_read_next")
    async def autoread_read_next(self, event: AstrMessageEvent, reason: str = ""):
        """读取当前书的下一段文本，生成阶段性读书笔记，并推进阅读进度。

        Args:
            reason(string): 本次主动阅读的原因，例如"用户问我最近读到哪里了""我想继续读一点""定时任务触发"。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        if not self.config.get("allow_llm_read_next", True):
            yield event.plain_result("当前配置不允许模型通过自然对话主动推进阅读。")
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.read_next_chunk(
            umo=umo,
            reason=reason,
            send_message=False,
            source="llm_tool",
        )
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_get_status")
    async def autoread_get_status(self, event: AstrMessageEvent):
        """查看当前会话的持续阅读状态。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_status(umo)
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_get_notes")
    async def autoread_get_notes(self, event: AstrMessageEvent, limit: float = 5):
        """查看当前书最近的持续阅读笔记。

        Args:
            limit(number): 返回最近多少条笔记，默认 5 条。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_notes(umo=umo, limit=int(limit))
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_pause")
    async def autoread_pause(self, event: AstrMessageEvent):
        """暂停当前会话的后台持续阅读。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        result = await self.autoread_service.pause(event.unified_msg_origin)
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_resume")
    async def autoread_resume(self, event: AstrMessageEvent):
        """恢复当前会话的后台持续阅读。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        result = await self.autoread_service.resume(event.unified_msg_origin)
        yield event.plain_result(result)

    @filter.llm_tool(name="autoread_stop")
    async def autoread_stop(self, event: AstrMessageEvent):
        """停止当前阅读任务，但保留历史笔记。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        if not self.config.get("enable_llm_tools", True):
            yield event.plain_result("自然对话工具入口当前已关闭。")
            return
        result = await self.autoread_service.stop(event.unified_msg_origin)
        yield event.plain_result(result)

    # ==================================================================
    # LLM Tool 监控钩子
    # ==================================================================

    @filter.on_using_llm_tool()
    async def on_using_llm_tool(
        self,
        event: AstrMessageEvent,
        tool,
        tool_args: dict | None,
    ):
        """记录 LLM Tool 被调用。"""
        tool_name = getattr(tool, "name", str(tool))
        if tool_name and tool_name.startswith("autoread_"):
            logger.info(f"[AutoRead] LLM tool called: {tool_name}, args={tool_args}")

    @filter.on_llm_tool_respond()
    async def on_llm_tool_respond(
        self,
        event: AstrMessageEvent,
        tool,
        tool_args: dict | None,
        tool_result,
    ):
        """记录 LLM Tool 调用完成。"""
        tool_name = getattr(tool, "name", str(tool))
        if tool_name and tool_name.startswith("autoread_"):
            logger.info(f"[AutoRead] LLM tool responded: {tool_name}")
