import asyncio
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .services.book_loader import BookLoader
from .services.text_chunker import TextChunker
from .repositories.reading_state_repository import ReadingStateStore
from .services.note_writer import NoteWriter
from .services.memory_bridge import MemoryBridge
from .services.autoread_service import AutoReadService
from .core.config_service import ConfigService
from .services.provider_resolver import ProviderResolver
from .services.model_router import ModelRouter
from .services.backup_service import BackupService
from .worker.reading_worker import ReadingWorker
from .core.page_service import WebUIService
from .core.page_api import AutoReadWebUIAPI
from .services.read_action_result import (
    ReadActionResult,
    OutputCategory,
    get_policy,
)
from .services.role_response_composer import RoleResponseComposer

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

        # P1-4.3: 标记当前对话轮次是否有 autoread 工具参与
        self._autoread_tool_invoked = False

        # 运行数据目录
        plugin_name = getattr(self, "name", None) or PLUGIN_NAME
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / plugin_name
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "books").mkdir(exist_ok=True)
        (self.data_dir / "chunks").mkdir(exist_ok=True)
        (self.data_dir / "notes").mkdir(exist_ok=True)

        # 配置服务（以 AstrBotConfig 为唯一主配置源）
        self.config_service = ConfigService(config=self.config)

        # Provider 解析器
        self.provider_resolver = ProviderResolver(
            context=self.context,
            config_service=self.config_service,
        )

        # 模型路由器
        self.model_router = ModelRouter(
            config_service=self.config_service,
        )

        # B-first 分层骨架：角色表达组件
        self.role_composer = RoleResponseComposer(
            context=self.context,
            config_service=self.config_service,
        )

        # 基础设施层
        self.state_store = ReadingStateStore(self.data_dir)
        self.book_loader = BookLoader(
            data_dir=self.data_dir,
            allowed_extensions=self.config_service.get("allowed_extensions", [".txt", ".md"]),
        )
        self.chunker = TextChunker(
            chunk_size=int(self.config_service.get("chunk_size", 1800)),
            chunk_overlap=int(self.config_service.get("chunk_overlap", 120)),
        )
        self.note_writer = NoteWriter(
            context=self.context,
            config_service=self.config_service,
            provider_resolver=self.provider_resolver,
        )
        self.memory_bridge = MemoryBridge(
            backend=self.config_service.get("memory_backend", "none"),
        )

        # 业务编排层
        self.autoread_service = AutoReadService(
            context=self.context,
            config_service=self.config_service,
            data_dir=self.data_dir,
            state_store=self.state_store,
            book_loader=self.book_loader,
            chunker=self.chunker,
            note_writer=self.note_writer,
            memory_bridge=self.memory_bridge,
        )

        # 后台 worker（使用 config_service 以支持动态配置）
        self.worker = ReadingWorker(
            context=self.context,
            config_service=self.config_service,
            service=self.autoread_service,
            state_store=self.state_store,
        )
        self._worker_task: asyncio.Task | None = None

        # 备份服务
        self.backup_service = BackupService(
            data_dir=self.data_dir,
            state_store=self.state_store,
        )

        # WebUI 管理层
        self.webui_service = WebUIService(
            data_dir=self.data_dir,
            state_store=self.state_store,
            autoread_service=self.autoread_service,
            book_loader=self.book_loader,
            chunker=self.chunker,
            config_service=self.config_service,
            provider_resolver=self.provider_resolver,
            backup_service=self.backup_service,
        )
        self.webui_api = AutoReadWebUIAPI(
            context=self.context,
            webui_service=self.webui_service,
        )

        # --- WebUI API 注册 ---
        webui_enabled = self.config_service.get("webui_enabled", True)
        logger.info(
            f"[AutoRead] WebUI enabled={webui_enabled}, "
            f"register_web_api={'available' if hasattr(self.context, 'register_web_api') else 'missing'}"
        )
        if webui_enabled:
            if not hasattr(self.context, "register_web_api"):
                logger.warning(
                    "[AutoRead] WebUI API 不可用：当前 AstrBot 版本不支持 register_web_api。"
                    " WebUI 页面将无法连接后端。"
                )
            else:
                try:
                    self.webui_api.register_routes()
                except Exception:
                    logger.exception(
                        "[AutoRead] WebUI API 路由注册失败！WebUI 页面将无法连接后端。"
                    )
        else:
            logger.info(
                "[AutoRead] WebUI API 已按配置关闭（webui_enabled=false）。"
                " 可在插件原生设置 → 页面设置 → 启用页面 中开启。"
            )

    # ==================================================================
    # 生命周期
    # ==================================================================

    @staticmethod
    def _get_event(event_or_context):
        """兼容 AstrBot v4.26+ LLM Tool 新协议。

        v4.26+ 传递 ContextWrapper[AstrAgentContext]，
        旧版传递 AstrMessageEvent。
        """
        ctx = getattr(event_or_context, "context", None)
        if ctx is not None:
            ev = getattr(ctx, "event", None)
            if ev is not None:
                return ev
        return event_or_context

    # ==================================================================
    # UMO 授权：管理/控制权限判断
    # ==================================================================
    #
    # enabled_umos 已从"自然读书能力启用范围"调整为"拥有管理/控制权限的 UMO 列表"。
    #
    # 授权 UMO（在 enabled_umos 列表中）：
    #   - 可使用 /read 命令（管理/调试入口）
    #   - 可通过自然语言执行管理操作（导入、删除、进度控制等）
    #   - 可触发平台文件自动入库
    #
    # 未授权 UMO（不在 enabled_umos 列表中）：
    #   - 可通过自然语言了解角色书架、阅读状态、笔记、读后感
    #   - 可围绕已读内容进行普通讨论
    #   - 不可执行管理/控制类操作
    #
    # 角色书架、阅读状态、阅读笔记属于角色自身能力状态，
    # 不因 UMO 是否授权而"不可见"。

    # 自然语言无权限消息（LLM Tool 返回）
    # 原则：表达"本次未改动书签/进度/书架"，不说角色不能阅读、
    # 不暴露 AstrBot/WebUI/权限/配置机制、不指导用户配置。
    _MSG_UNAUTHORIZED_TOOL = (
        "这次我没有改动书签或阅读进度。"
        "书还在书架上，我们可以慢慢聊它。"
    )

    # /read 命令无权限消息（管理入口，可比自然语言路径更明确）
    _MSG_UNAUTHORIZED_COMMAND = (
        "当前会话没有 AutoRead 管理权限。\n"
        "如需使用 /read 命令管理书架和阅读进度，"
        "请在插件设置中将当前会话添加到 enabled_umos 列表。\n"
        "你可以直接和我聊书架上的书，也可以问我读过什么、记过什么。"
    )

    # ---- 基础方法 ----

    def _resolve_umo(self, event) -> str | None:
        """从事件中解析 UMO（unified_msg_origin）。

        优先使用 AstrBot 标准字段 unified_msg_origin。
        """
        umo = getattr(event, "unified_msg_origin", None)
        if umo and isinstance(umo, str) and umo.strip():
            return umo.strip()
        return None

    def _is_umo_enabled(self, event) -> bool:
        """判断当前事件 UMO 是否在 enabled_umos 列表中（兼容旧方法名）。

        委托到 _is_umo_authorized。
        """
        return self._is_umo_authorized(event)

    def _is_umo_authorized(self, event) -> bool:
        """判断当前事件 UMO 是否拥有 AutoRead 管理/控制权限。

        规则：
        - enabled_umos 为空 → False（默认不授权任何 UMO）
        - 无法解析 UMO → False
        - UMO 在列表中 → True

        注意：此方法仅用于管理/控制类操作。
        书架查询、阅读状态、笔记查看等只读能力
        不依赖此检查，对所有 UMO 默认可用。
        """
        enabled_umos = self.config_service.get("enabled_umos", [])
        if not enabled_umos:
            return False

        umo = self._resolve_umo(event)
        if umo is None:
            logger.debug("[AutoRead UMO] Cannot resolve UMO from event")
            return False

        if umo in enabled_umos:
            return True

        logger.debug("[AutoRead UMO] UMO not in enabled_umos (management declined)")
        return False

    def _check_umo_or_skip(self, event) -> str | None:
        """UMO 守卫（兼容旧方法）：返回跳过消息或 None 表示通过。

        已委托到 _check_authorized_or_deny。
        """
        return self._check_authorized_or_deny(event)

    def _check_authorized_or_deny(self, event) -> str | None:
        """授权守卫：返回自然无权限消息（LLM Tool 上下文），或 None 表示通过。

        LLM Tool handler 中调用:
            deny_msg = self._check_authorized_or_deny(event)
            if deny_msg is not None:
                return deny_msg
        """
        if not self._is_umo_authorized(event):
            return self._MSG_UNAUTHORIZED_TOOL
        return None

    def _check_command_authorized(self, event) -> str | None:
        """命令授权守卫：返回自然无权限消息，或 None 表示通过。"""
        if not self._is_umo_authorized(event):
            return self._MSG_UNAUTHORIZED_COMMAND
        return None

    async def initialize(self):
        """插件初始化时启动后台 worker。"""
        if self.config_service.get("enabled", True):
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
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.bind(umo)
        yield event.plain_result(result)

    @read.command("import")
    async def read_import(self, event: AstrMessageEvent, filename: str = ""):
        """导入本地书籍。需要管理权限。

        Args:
            filename(string): 要导入的文件名（文件须位于 plugin_data/astrbot_plugin_autoread/books/ 下）

        Examples:
            /read import mybook.txt
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
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
        """列出已导入的书籍。需要管理权限。"""
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        result = await self.autoread_service.list_books()
        yield event.plain_result(result)

    @read.command("choose")
    async def read_choose(self, event: AstrMessageEvent, preference: str = ""):
        """根据偏好选择书籍。需要管理权限。

        Args:
            preference(string): 阅读偏好（可选）
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        umo = event.unified_msg_origin
        result = await self.autoread_service.choose_book(umo=umo, preference=preference)
        yield event.plain_result(result)

    @read.command("start")
    async def read_start(self, event: AstrMessageEvent, book_id: str = ""):
        """开始持续阅读一本书。需要管理权限。

        Args:
            book_id(string): 要阅读的书籍 ID

        Examples:
            /read start book_20260623_001
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
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
        """阅读下一段文本并生成笔记。需要管理权限。"""
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
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
        """查看当前阅读进度（B-first 分层验证切片）。需要管理权限。

        走新骨架: ReadActionResult → OutputPolicy → RoleResponseComposer。
        管理类/禁用/无任务场景保持插件直出。
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        umo = event.unified_msg_origin

        # 1. 获取原始业务数据
        fallback_text = await self.autoread_service.get_status(umo)
        session = await self.state_store.get_session(umo)

        # 2. 构建结构化结果
        result = ReadActionResult(
            action="status",
            success=True,
            message=fallback_text,
            output_category=OutputCategory.COMMAND_QUERY,
        )

        # 如果无进行中任务，保持管理类直出
        if session is None or not session.get("current_book_id"):
            result.output_category = OutputCategory.MANAGEMENT
            result.apply_policy()
            logger.info(
                f"[AutoRead] /read status: no active session, "
                f"fallback to direct output. {result.policy_summary()}"
            )
            yield event.plain_result(fallback_text)
            return

        # 3. 填充结构化信息
        result.book_title = session.get("current_book_title")
        current_idx = session.get("current_chunk_index", 0)
        total = session.get("total_chunks", 0)
        result.progress = f"第 {current_idx}/{total} 段"

        if total > 0:
            pct = round(current_idx / total * 100, 1)
            result.progress += f"（{pct}%）"

        if session.get("paused"):
            result.progress += " [已暂停]"

        result.apply_policy()

        # 4. 尝试 LLM 表达
        response = await self.role_composer.compose(result, umo=umo)
        was_composed = getattr(result, "_composed", False)

        logger.info(
            f"[AutoRead] /read status: composed={was_composed} "
            f"{result.policy_summary()}"
        )
        yield event.plain_result(response)

    @read.command("notes")
    async def read_notes(self, event: AstrMessageEvent, limit: str = "5"):
        """查看最近阅读笔记。需要管理权限。

        Args:
            limit(string): 返回条数，默认 5
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        try:
            n = int(limit)
        except ValueError:
            n = 5
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_notes(umo=umo, limit=n)
        yield event.plain_result(result)

    @read.command("pause")
    async def read_pause(self, event: AstrMessageEvent):
        """暂停后台阅读。需要管理权限。"""
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        result = await self.autoread_service.pause(event.unified_msg_origin)
        yield event.plain_result(result)

    @read.command("resume")
    async def read_resume(self, event: AstrMessageEvent):
        """恢复后台阅读。需要管理权限。"""
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        result = await self.autoread_service.resume(event.unified_msg_origin)
        yield event.plain_result(result)

    @read.command("stop")
    async def read_stop(self, event: AstrMessageEvent):
        """停止当前阅读任务。需要管理权限。"""
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        result = await self.autoread_service.stop(event.unified_msg_origin)
        yield event.plain_result(result)

    @read.command("reread")
    async def read_reread(self, event: AstrMessageEvent, args: str = ""):
        """重新阅读指定范围。不推进主进度，不删除旧笔记。

        用法:
          /read reread --note <note_id>        按笔记 ID 重读对应段
          /read reread --book <book_id> --from 35% --to 40%
          /read reread --book <book_id> --from-index 10 --to-index 15
          /read reread --help                  查看帮助
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        umo = event.unified_msg_origin
        parsed = self._parse_reread_args(args)

        if parsed.get("help"):
            yield event.plain_result(
                "/read reread 用法:\n"
                "  --note <note_id>           按笔记 ID 重读对应原文段落\n"
                "  --book <book_id>           指定书籍\n"
                "  --from <N>%                起始百分比\n"
                "  --to <N>%                  结束百分比\n"
                "  --from-index <N>           起始段索引\n"
                "  --to-index <N>             结束段索引\n"
                "\n重读不推进主进度，不删除旧笔记。"
            )
            return

        if not parsed.get("book_id") and not parsed.get("note_id"):
            yield event.plain_result(
                "请指定 --book <book_id> 或 --note <note_id>。\n"
                "使用 /read reread --help 查看完整用法。"
            )
            return

        result = await self.autoread_service.reread_range(
            umo=umo,
            book_id=parsed.get("book_id", ""),
            note_id=parsed.get("note_id"),
            start_index=parsed.get("start_index"),
            end_index=parsed.get("end_index"),
            start_percent=parsed.get("start_percent"),
            end_percent=parsed.get("end_percent"),
            source="command",
        )
        yield event.plain_result(result)

    @read.command("progress")
    async def read_progress(self, event: AstrMessageEvent, args: str = ""):
        """查看或设置阅读进度。需要管理权限。

        用法:
          /read progress                      查看当前进度
          /read progress set --book <book_id> --percent 35%
          /read progress set --book <book_id> --index 10
        """
        deny_msg = self._check_command_authorized(event)
        if deny_msg is not None:
            yield event.plain_result(deny_msg)
            return
        umo = event.unified_msg_origin
        args = (args or "").strip()

        if not args or args == "list":
            result = await self.autoread_service.get_status(umo, source="command")
            yield event.plain_result(result)
            return

        if args.startswith("set "):
            parsed = self._parse_progress_args(args[4:])
            if not parsed.get("book_id"):
                yield event.plain_result("请指定 --book <book_id>")
                return
            if parsed.get("chunk_index") is None and parsed.get("percent") is None:
                yield event.plain_result("请指定 --percent <N>% 或 --index <N>")
                return
            result = await self.autoread_service.set_progress(
                umo=umo,
                book_id=parsed["book_id"],
                chunk_index=parsed.get("chunk_index"),
                percent=parsed.get("percent"),
            )
            yield event.plain_result(result)
            return

        yield event.plain_result(
            "用法: /read progress [set --book <id> --percent N% | --index N]\n"
            "不带参数时查看当前进度。"
        )

    @staticmethod
    def _parse_reread_args(args: str) -> dict:
        import re
        result: dict = {}
        tokens = args.split()
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == "--help":
                result["help"] = True
            elif t == "--book" and i + 1 < len(tokens):
                i += 1; result["book_id"] = tokens[i].strip()
            elif t == "--note" and i + 1 < len(tokens):
                i += 1; result["note_id"] = tokens[i].strip()
            elif t == "--from" and i + 1 < len(tokens):
                i += 1; v = tokens[i].strip()
                if v.endswith("%"):
                    try: result["start_percent"] = float(v[:-1])
                    except ValueError: pass
                else:
                    try: result["start_index"] = int(v)
                    except ValueError: pass
            elif t == "--to" and i + 1 < len(tokens):
                i += 1; v = tokens[i].strip()
                if v.endswith("%"):
                    try: result["end_percent"] = float(v[:-1])
                    except ValueError: pass
                else:
                    try: result["end_index"] = int(v)
                    except ValueError: pass
            elif t == "--from-index" and i + 1 < len(tokens):
                i += 1
                try: result["start_index"] = int(tokens[i])
                except ValueError: pass
            elif t == "--to-index" and i + 1 < len(tokens):
                i += 1
                try: result["end_index"] = int(tokens[i])
                except ValueError: pass
            i += 1
        return result

    @staticmethod
    def _parse_progress_args(args: str) -> dict:
        import re
        result: dict = {}
        tokens = args.split()
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t == "--book" and i + 1 < len(tokens):
                i += 1; result["book_id"] = tokens[i].strip()
            elif t == "--percent" and i + 1 < len(tokens):
                i += 1; v = tokens[i].strip().rstrip("%")
                try: result["percent"] = float(v)
                except ValueError: pass
            elif t == "--index" and i + 1 < len(tokens):
                i += 1
                try: result["chunk_index"] = int(tokens[i])
                except ValueError: pass
            i += 1
        return result

    # ==================================================================
    # LLM Tool 入口 —— 自然对话触发
    # ==================================================================

    @filter.llm_tool(name="autoread_list_books")
    async def autoread_list_books(self, _event_or_ctx, dummy: str = ""):
        """列出当前可供持续阅读的书籍。所有会话均可查询书架。

        无需参数，直接调用即可。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        result = await self.autoread_service.list_books(source="llm_tool")
        return str(result)

    @filter.llm_tool(name="autoread_search_books")
    async def autoread_search_books(self, _event_or_ctx, query: str = ""):
        """根据书名、作者名或部分关键词搜索书架上的书籍。所有会话均可使用。

        适用场景：用户说"继续读小王子""小王子读到哪里了""三体""刘慈欣的书"等提到具体书名/作者时，
        先用本工具搜索匹配的 book_id，再用其他工具（autoread_start_book / autoread_read_next / autoread_get_status）操作。

        注意：返回结果中的 book_id 是内部标识符，不要在回复中向用户展示 book_id。
        向用户提及书籍时，用书名（如《小王子》）即可。

        Args:
            query(string): 搜索词，例如"小王子""三体""圣埃克苏佩里"。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        if not query or not query.strip():
            return "没有提供搜索词，不确定你想找哪本书。"

        books = await self.state_store.list_books()
        if not books:
            return "书架是空的，没有书可以搜索。"
            return

        from .services.book_metadata import search_books, normalize_book_meta
        for b in books:
            normalize_book_meta(b)

        results = search_books(query, books)
        if not results:
            return f"没有找到和「{query}」匹配的书。"

        if len(results) == 1:
            r = results[0]
            author_str = f"，作者是{r.author}" if r.author else ""
            return (
                f"找到了《{r.display_name}》{author_str}。"
                f"\n[内部使用] book_id: {r.book_id}"
            )

        # 多候选：列出书名和对应 book_id，供后续精确选择
        items = []
        for r in results[:5]:
            author_str = f"（{r.author}）" if r.author else ""
            items.append(f"《{r.display_name}》{author_str} [book_id: {r.book_id}]")
        return (
            f"找到了 {len(results)} 本和「{query}」相关的书：\n" + "\n".join(f"  · {item}" for item in items)
        )

    @filter.llm_tool(name="autoread_choose_book")
    async def autoread_choose_book(self, _event_or_ctx, preference: str = ""):
        """根据当前角色兴趣和用户给出的偏好，从已导入书籍中推荐一本。该工具只推荐书名，不会自动开始阅读，也不会改动书签或进度。
        需要管理权限。如果工具返回表示本次没有改动书签或进度，不要继续调用控制类 AutoRead 工具，以角色口吻自然延续对话即可。

        如果用户只是说"你推荐一本""你挑一本"，本工具可以独立使用。
        如果用户明确说"读读看""开始读"，则需要在 choose_book 之后继续调用 autoread_start_book → autoread_read_next。

        Args:
            preference(string): 用户或角色表达的阅读偏好，例如"童话""科幻""哲学""轻松一点""你自己感兴趣的"。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        umo = event.unified_msg_origin
        result = await self.autoread_service.choose_book(umo=umo, preference=preference, source="llm_tool")
        return str(result)

    @filter.llm_tool(name="autoread_start_book")
    async def autoread_start_book(
        self, _event_or_ctx, book_id: str, interval_minutes: float = 1440
    ):
        """开始持续阅读一本已导入的书（仅创建阅读会话，不读取任何书籍内容）。需要管理权限。

        适用场景：用户说"读一下小王子""开始读小王子""我们读三体吧"等。
        如果用户提到书名而不是 book_id，请先调用 autoread_search_books 查找 book_id。

        **重要**：本工具只创建阅读会话并设置进度指针为第 0 段，**不返回任何书籍内容**。
        调用成功后，你实际上还没有读到任何文字。要继续阅读，必须接着调用 autoread_read_next
        获取第一段的内容。停在 start_book 就回复用户等于什么都没读。

        典型完整流程（授权 UMO 中用户说"读读看小王子"）：
        autoread_search_books("小王子") → autoread_start_book(book_id) → autoread_read_next()

        如果工具返回表示本次没有改动书签或进度，不要继续调用 read_next/set_progress 等控制类工具、
        不要说角色不能阅读、不要指导用户配置 AstrBot/WebUI/权限；以角色口吻自然延续对话即可，例如聊聊书或感受。

        Args:
            book_id(string): 要开始阅读的书籍 ID。请通过 autoread_search_books（按书名/作者搜索）或 autoread_list_books（查看书架）获取。不要在回复中向用户展示 book_id。
            interval_minutes(number): 阅读间隔，单位分钟。默认 1440 分钟，即每天一次。一般情况下不需要填写。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        umo = event.unified_msg_origin
        result = await self.autoread_service.start_book(
            umo=umo,
            book_id=book_id,
            interval_minutes=interval_minutes,
            source="llm_tool",
        )
        return str(result)

    @filter.llm_tool(name="autoread_read_next")
    async def autoread_read_next(self, _event_or_ctx, reason: str = ""):
        """继续读当前正在读的书的下一段。需要管理权限。

        适用场景：用户说"继续读吧""继续""接着读""再读一段"等。

        调用本工具前，必须先确认当前有一本正在读的书（即已经通过 autoread_start_book 设置了阅读会话）。
        如果当前没有正在读的书：
        - 用户提到了具体书名 → 先 autoread_search_books 找书，再 autoread_start_book 开始，
          然后调用本工具读第一段。完整链路：search_books → start_book → read_next。
        - 用户没提具体书名 → 先 autoread_list_books 查看书架上有哪些书，自然询问用户想读哪本，
          不要自己随便选一本开始。
        - 不要在确认有 active book 之前直接调用本工具。

        如果工具返回表示本次没有改动书签或进度，不要继续调用其他控制类工具、
        不要说角色不能阅读、不要指导用户配置 AstrBot/WebUI/权限；以角色口吻自然延续对话，可聊书或聊感受。

        注意：工具返回的是内部结构化阅读结果。你必须把结果转化为符合当前人格的自然表达，
        不要原样输出"摘要/细节/反思/书名/进度/章节/分享素材"等字段名，不要写成报告。
        只有工具返回的内容可以称为已经读到，不要假装读过后文或评价整本书。

        Args:
            reason(string): 本次主动阅读的原因，例如"用户让我继续读""我想接着读一点""定时任务触发"。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        if not self.config_service.get("allow_llm_read_next", True):
            return "当前配置不允许模型通过自然对话主动推进阅读。"
        umo = event.unified_msg_origin
        result = await self.autoread_service.read_next_chunk(
            umo=umo,
            reason=reason,
            send_message=False,
            source="llm_tool",
        )
        return str(result)

    @filter.llm_tool(name="autoread_get_status")
    async def autoread_get_status(self, _event_or_ctx):
        """查看当前的持续阅读状态。所有会话均可查询。

        注意：工具返回的是内部状态数据。你必须把结果转化为自然表达，
        不要原样输出"书名/进度/状态/上次阅读时间/下次阅读时间"等字段名。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_status(umo, source="llm_tool")
        return str(result)

    @filter.llm_tool(name="autoread_get_notes")
    async def autoread_get_notes(self, _event_or_ctx, limit: float = 5, book_id: str = ""):
        """查看阅读笔记。所有会话均可查询。

        适用场景：用户说"你有什么阅读笔记？""你怎么看刚才那段？""你之前读小王子时记了什么？"
        如果用户提到具体书名，请先调用 autoread_search_books 查找 book_id，再传入本工具。
        不传 book_id 时返回当前正在读的书的笔记。

        注意：工具返回的是阅读笔记的事实摘要。你必须把结果转化为符合当前人格的自然表达，
        不要原样输出"读到的内容概括/注意到的细节/我的感受"等字段名，不要写成报告。
        只提及工具实际返回的内容，不要编造未读到的情节。

        Args:
            limit(number): 返回最近多少条笔记，默认 5 条。
            book_id(string): 书籍 ID（可选）。不传则查询当前正在读的书。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        umo = event.unified_msg_origin
        result = await self.autoread_service.get_notes(
            umo=umo, limit=int(limit), source="llm_tool", book_id=book_id.strip(),
        )
        return str(result)

    @filter.llm_tool(name="autoread_pause")
    async def autoread_pause(self, _event_or_ctx):
        """暂停当前会话的后台持续阅读。需要管理权限。
        如果工具返回表示本次没有改动书签或进度，不要继续调用控制类工具，以角色口吻自然延续对话。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        result = await self.autoread_service.pause(event.unified_msg_origin)
        return str(result)

    @filter.llm_tool(name="autoread_resume")
    async def autoread_resume(self, _event_or_ctx):
        """恢复当前会话的后台持续阅读。需要管理权限。
        如果工具返回表示本次没有改动书签或进度，不要继续调用控制类工具，以角色口吻自然延续对话。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        result = await self.autoread_service.resume(event.unified_msg_origin)
        return str(result)

    @filter.llm_tool(name="autoread_stop")
    async def autoread_stop(self, _event_or_ctx):
        """停止当前阅读任务，但保留历史笔记。需要管理权限。
        如果工具返回表示本次没有改动书签或进度，不要继续调用控制类工具，以角色口吻自然延续对话。

        Args:
            dummy(string): 无需填写，保留为空字符串。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        result = await self.autoread_service.stop(event.unified_msg_origin)
        return str(result)

    @filter.llm_tool(name="autoread_reread")
    async def autoread_reread(
        self, _event_or_ctx, book_id: str = "", note_id: str = "",
        start_percent: float = 0, end_percent: float = 0,
        start_index: float = -1, end_index: float = -1,
    ):
        """重新阅读指定范围。不推进主进度，不删除旧笔记。与继续阅读（autoread_read_next）不同。
        需要管理权限。如果工具返回表示本次没有改动书签或进度，不要继续调用控制类工具，以角色口吻自然延续对话。

        适用场景：用户说"重新读一下第三章""这段重新读一遍""这条笔记对应的原文再读一次"。

        Args:
            book_id(string): 书籍 ID。如果通过 note_id 定位可省略。
            note_id(string): 笔记 ID。根据笔记的 chunk_index 定位原文范围。
            start_percent(number): 起始百分比（0-100）。与 start_index 二选一。
            end_percent(number): 结束百分比（0-100）。与 end_index 二选一。
            start_index(number): 起始段索引。与 start_percent 二选一。
            end_index(number): 结束段索引。与 end_percent 二选一。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        umo = event.unified_msg_origin

        si = int(start_index) if start_index >= 0 else None
        ei = int(end_index) if end_index >= 0 else None
        sp = float(start_percent) if start_percent > 0 else None
        ep = float(end_percent) if end_percent > 0 else None

        result = await self.autoread_service.reread_range(
            umo=umo,
            book_id=book_id,
            note_id=note_id or None,
            start_index=si,
            end_index=ei,
            start_percent=sp,
            end_percent=ep,
            source="llm_tool",
        )
        return str(result)

    @filter.llm_tool(name="autoread_set_progress")
    async def autoread_set_progress(
        self, _event_or_ctx, book_id: str = "", percent: float = 0, chunk_index: float = -1
    ):
        """设置当前阅读进度。不读取内容，不生成笔记。仅修改进度指针。
        需要管理权限。如果工具返回表示本次没有改动书签或进度，不要继续调用控制类工具，以角色口吻自然延续对话。

        适用场景：用户说"把进度调到35%""从第10段开始"。

        Args:
            book_id(string): 书籍 ID。
            percent(number): 目标百分比（0-100）。与 chunk_index 二选一。
            chunk_index(number): 目标段索引。与 percent 二选一。
        """
        event = self._get_event(_event_or_ctx)
        if not self.config_service.get("enable_llm_tools", True):
            return "自然对话工具入口当前已关闭。"
        deny_msg = self._check_authorized_or_deny(event)
        if deny_msg is not None:
            return deny_msg
        umo = event.unified_msg_origin

        ci = int(chunk_index) if chunk_index >= 0 else None
        pct = float(percent) if percent > 0 else None

        result = await self.autoread_service.set_progress(
            umo=umo,
            book_id=book_id,
            chunk_index=ci,
            percent=pct,
        )
        return str(result)

    # ==================================================================
    # P1-2：上传文件自动入库（静默）
    # ==================================================================

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL, priority=90)
    async def _on_file_auto_import(self, event: AstrMessageEvent):
        """上传文件自动入库。

        只在 enabled_umos 命中 + auto_import_uploaded_books=true 时生效。
        入库后不主动回复——这是书架更新事件，不是对话回复事件。
        """
        # 1. 配置检查
        if not self.config_service.get("auto_import_uploaded_books", False):
            return
        if not self.config_service.get("enabled", True):
            return

        # 2. UMO 检查
        if not self._is_umo_enabled(event):
            logger.debug("[AutoRead Import] Skipped: UMO not enabled")
            return

        # 3. 提取文件组件
        try:
            messages = event.get_messages()
        except Exception:
            logger.debug("[AutoRead Import] Cannot get messages from event")
            return
        if not messages:
            return

        file_components = [
            m for m in messages
            if hasattr(m, "name") and hasattr(m, "get_file")
        ]
        if not file_components:
            return

        logger.debug(
            f"[AutoRead Import] Found {len(file_components)} file component(s) "
            f"in message"
        )

        # 4. 逐个处理文件
        for comp in file_components:
            await self._try_auto_import_file(comp, event)

    async def _try_auto_import_file(self, comp, event: AstrMessageEvent):
        """尝试导入单个文件组件。

        文件名策略（与 WebUI 上传统一）：
        - 优先保留平台上传文件的原始可读文件名
        - 重名时追加 __N 短后缀
        - 不再使用 upload_xxx / 随机 ID 作为正式书籍文件名

        失败时静默记录日志并回滚本次产生的半成品。
        """
        filename = getattr(comp, "name", None) or "unknown"
        suffix = Path(filename).suffix.lower()
        allowed = [
            e.lower() for e in
            self.config_service.get("allowed_extensions", [".txt", ".md"])
        ]

        if suffix not in allowed:
            logger.debug(f"[AutoRead Import] Skipped unsupported: {filename}")
            return

        # 下载/获取本地路径
        try:
            file_path = await comp.get_file()
        except Exception as exc:
            logger.warning(f"[AutoRead Import] Failed to get file {filename}: {exc}")
            await self._record_bookshelf_event(
                "auto_import_failed", book_id="", title=filename,
                original_filename=filename,
                event=event, error=str(exc),
            )
            return

        if not file_path or not Path(file_path).exists():
            logger.warning(f"[AutoRead Import] File not accessible: {filename}")
            return

        # ----- 确定存储文件名：保留原始可读文件名 -----
        import shutil

        safe_name = Path(filename).name
        if not safe_name or safe_name.startswith("."):
            # 极端情况：文件名只有扩展名或以 . 开头，使用安全回退名
            import uuid as _uuid
            safe_name = f"file_{_uuid.uuid4().hex[:8]}{suffix}"

        books_dir = self.data_dir / "books"
        stored_name = safe_name

        # 重名冲突：追加 __N 短后缀
        if (books_dir / stored_name).exists():
            base = Path(safe_name).stem
            counter = 2
            while (books_dir / f"{base}__{counter}{suffix}").exists():
                counter += 1
            stored_name = f"{base}__{counter}{suffix}"

        stored_path = books_dir / stored_name

        # 复制到 books/ 目录
        try:
            shutil.copy2(file_path, stored_path)
        except Exception as exc:
            logger.warning(f"[AutoRead Import] Copy failed {filename}: {exc}")
            await self._record_bookshelf_event(
                "auto_import_failed", book_id="", title=filename,
                original_filename=filename,
                event=event, error=str(exc),
            )
            return

        # 复用现有导入流程（import_book → import_local_book → chunk → register）
        try:
            result = await self.autoread_service.import_book(stored_name)
        except Exception as exc:
            logger.warning(f"[AutoRead Import] Import failed {filename}: {exc}")
            # 回滚：删除本次保存的 books 文件
            try:
                stored_path.unlink(missing_ok=True)
            except Exception:
                pass
            # 回滚：清理 state.json 中可能已写入的 book 记录
            await self._cleanup_book_by_source_path(stored_name)
            await self._record_bookshelf_event(
                "auto_import_failed", book_id="", title=filename,
                original_filename=filename,
                event=event, error=str(exc),
            )
            return

        # 重名冲突时：从原始文件名重建元数据，避免 title/author 被 __N 后缀污染
        if stored_name != safe_name:
            await self._fix_book_metadata_from_original_filename(
                stored_name, safe_name,
            )

        # 提取 book_id（import_book 返回的格式化文本中包含）
        import re as _re
        bid_match = _re.search(r"book_id:\s*(\S+)", result)
        book_id = bid_match.group(1) if bid_match else ""

        logger.info(
            f"[AutoRead Import] Auto-imported: {filename} → {stored_name}"
            + (f" (book_id={book_id})" if book_id else "")
        )

        await self._record_bookshelf_event(
            "auto_import_success", book_id=book_id,
            title=Path(filename).stem[:120], original_filename=filename,
            event=event, detail=result,
        )

    # ------------------------------------------------------------------
    # 平台自动入库辅助：回滚与元数据修正
    # ------------------------------------------------------------------

    async def _cleanup_book_by_source_path(self, stored_name: str) -> None:
        """从 state.json 中移除 source_path 指向本次残留文件的 book 记录。

        用于导入失败后的回滚。只删本次产生的记录，不误删历史数据。
        """
        try:
            state = await self.state_store.load_state()
            source_path = f"books/{stored_name}"
            to_remove = None
            for bid, book in state.get("books", {}).items():
                if book.get("source_path") == source_path:
                    to_remove = bid
                    break
            if to_remove:
                del state["books"][to_remove]
                await self.state_store.save_state(state)
                logger.info(
                    f"[AutoRead Import] Rollback: removed book {to_remove} "
                    f"from state (source_path={source_path})"
                )
        except Exception:
            logger.warning(
                "[AutoRead Import] Rollback: failed to clean state for "
                f"stored_name={stored_name}"
            )

    async def _fix_book_metadata_from_original_filename(
        self, stored_name: str, original_name: str,
    ) -> None:
        """当存储名与原始文件名不同时（重名冲突），从原始文件名重建元数据。

        确保 title/author/display_name/aliases/normalized_keys
        不受 __N 冲突后缀影响。
        """
        try:
            from .services.book_metadata import build_book_metadata

            orig_meta = build_book_metadata(original_name)
            state = await self.state_store.load_state()
            source_path = f"books/{stored_name}"

            for bid, book in state.get("books", {}).items():
                if book.get("source_path") == source_path:
                    book["original_filename"] = original_name
                    book["file_stem"] = Path(original_name).stem
                    book["title"] = orig_meta["title"]
                    book["author"] = orig_meta["author"]
                    book["display_name"] = orig_meta["display_name"]
                    book["aliases"] = orig_meta["aliases"]
                    book["normalized_keys"] = orig_meta["normalized_keys"]
                    await self.state_store.save_state(state)
                    logger.info(
                        f"[AutoRead Import] Fixed metadata for {bid}: "
                        f"'{stored_name}' → original '{original_name}'"
                    )
                    break
        except Exception:
            logger.warning(
                "[AutoRead Import] Failed to fix metadata for conflict "
                f"resolution: stored={stored_name} original={original_name}"
            )

    async def _record_bookshelf_event(
        self, event_type: str, *,
        book_id: str = "", title: str = "",
        original_filename: str = "",
        event: AstrMessageEvent | None = None,
        error: str = "", detail: str = "",
    ):
        """记录书架事件（用于后续自然语言查询）。"""
        import uuid as _uuid
        from datetime import datetime, timezone, timedelta

        tz = timezone(timedelta(hours=8))
        umo = self._resolve_umo(event) if event else ""
        entry = {
            "event_id": f"bs_{_uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "book_id": book_id,
            "title": title,
            "original_filename": original_filename,
            "umo": umo or "",
            "timestamp": datetime.now(tz).isoformat(),
            "status": "error" if error else "ok",
            "error": error,
            "detail": detail[:500] if detail else "",
        }
        await self.state_store.append_bookshelf_event(entry)

    # ==================================================================
    # LLM Tool 监控钩子 + 平台消息流水补写
    # ==================================================================

    @filter.on_using_llm_tool()
    async def on_using_llm_tool(
        self,
        event: AstrMessageEvent,
        tool,
        tool_args: dict | None,
    ):
        """记录 LLM Tool 被调用，并标记本轮有 autoread 工具参与。"""
        tool_name = getattr(tool, "name", str(tool))
        if tool_name and tool_name.startswith("autoread_"):
            logger.info(f"[AutoRead] LLM tool called: {tool_name}, args={tool_args}")
            # P1-4.3: 标记本轮有 autoread 工具调用，用于 after_message_sent 补写平台流水
            self._autoread_tool_invoked = True

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

    @filter.after_message_sent(priority=50)
    async def _after_autoread_message_sent(self, event: AstrMessageEvent):
        """在 autoread 工具参与的对话轮次中，补写最终回复到平台消息流水。

        只写最终 assistant 回复，不写 Tool result，不写 /read 命令。
        """
        if not getattr(self, "_autoread_tool_invoked", False):
            return
        self._autoread_tool_invoked = False

        try:
            result = event.get_result()
            if not result or not result.chain:
                return

            # 提取最终回复文本
            from astrbot.core.message.components import Plain
            text_parts = []
            for comp in result.chain:
                if isinstance(comp, Plain):
                    text_parts.append(comp.text)
            final_text = "".join(text_parts).strip()
            if not final_text:
                return

            # 写入平台消息流水
            history_mgr = getattr(self.context, "message_history_manager", None)
            if not history_mgr:
                return

            umo = event.unified_msg_origin
            await history_mgr.insert(
                platform_id=event.get_platform_name(),
                user_id=event.get_sender_id(),
                content={"type": "bot", "message": final_text},
                sender_id="bot",
                sender_name="bot",
            )
            logger.debug(
                f"[AutoRead] Platform history persisted: "
                f"umo={umo} len={len(final_text)}"
            )
        except Exception:
            logger.debug("[AutoRead] Platform history persist skipped (non-critical)")
