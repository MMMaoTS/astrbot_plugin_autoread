"""AutoRead 业务编排核心。

命令入口、LLM Tool 入口、后台 worker 都必须复用本层，不得重复实现阅读逻辑。

工具返回内容 vs 用户可见回复 边界:
- source="command" (/read step 等) -> 结构化调试信息
- source="llm_tool" -> 内部上下文，附带自然回应指令
- source="worker" -> 自然语言分享文本
"""

import json
from pathlib import Path

from astrbot.api import logger


# ----------------------------------------------------------------
# 格式化辅助函数
# ----------------------------------------------------------------

def _fmt_tool_header(book_title: str, chunk_index: int, total_chunks: int) -> str:
    return (
        f'[内部阅读结果, 请不要原样复述字段名, 用当前人格自然回应]\n'
        f'书名: {book_title}\n'
        f'当前进度: 第 {chunk_index + 1}/{total_chunks} 段\n'
    )


def _fmt_command_result(
    book_title: str,
    chunk_index: int,
    total_chunks: int,
    chapter: str,
    note: dict,
) -> str:
    """给 /read step 使用的结构化调试输出。"""
    lines = [
        f'[BOOK] {book_title} 第 {chunk_index + 1}/{total_chunks} 段',
        f'章节: {chapter or "未知"}',
        f'',
        f'摘要: {note.get("summary", "")}',
    ]
    if note.get("detail"):
        lines.append(f'细节: {note.get("detail", "")}')
    if note.get("reflection"):
        lines.append(f'感想: {note.get("reflection", "")}')
    if note.get("should_share"):
        lines.append(f'分享文案: {note.get("share_message", "")}')
    return "\n".join(lines)


def _fmt_tool_context(
    book_title: str,
    chunk_index: int,
    total_chunks: int,
    chapter: str,
    note: dict,
) -> str:
    """给 LLM Tool 使用: 结构化信息 + 自然回应指令。"""
    lines = [
        _fmt_tool_header(book_title, chunk_index, total_chunks),
        f'章节: {chapter or "未知"}',
        f'本段概括: {note.get("summary", "")}',
        f'注意到的细节: {note.get("detail", "") or "(无)"}',
        f'角色感受/疑问: {note.get("reflection", "") or "(无)"}',
        f'建议自然回复素材: {note.get("share_message", "") or note.get("summary", "")}',
        '',
        (
            '请根据以上内部信息，用当前人格自然回复用户。'
            '不要输出「摘要」「细节」「反思」「书名」「进度」「章节」等字段名，'
            '不要写成报告。'
        ),
        (
            '如果本段信息不足以判断整本书，'
            '请诚实体现「目前只读到很前面的部分」。'
        ),
        '只有工具返回的内容可以称为已经读到，不要假装读过后文。',
    ]
    return "\n".join(lines)


def _fmt_share_message(
    book_title: str,
    chunk_index: int,
    total_chunks: int,
    note: dict,
) -> str:
    """给后台主动分享使用: 优先使用 share_message。"""
    share_msg = note.get("share_message", "") or note.get("summary", "")
    return (
        f'我刚刚继续读了一小段《{book_title}》。\n\n'
        f'{share_msg}\n\n'
        f'(进度: 第 {chunk_index}/{total_chunks} 段)'
    )


def _fmt_notes_tool_context(notes: list[dict], limit: int, book_title: str = "") -> str:
    """给 autoread_get_notes LLM Tool 使用的内部上下文。"""
    if not notes:
        return (
            '[内部阅读笔记查询结果]\n'
            '当前没有已保存的阅读笔记。\n'
            '请自然告知用户还没有笔记，不要编造。'
        )

    header = f'[内部阅读笔记, 请不要原样复述字段名, 用当前人格自然回应]\n'
    if book_title:
        header += f'书籍: {book_title}\n'
    header += f'最近 {len(notes)} 条笔记:\n'

    body_lines = []
    for i, n in enumerate(notes, 1):
        ts = n.get("created_at", "")[:16]
        body_lines.append(
            f'--- 笔记{i} ---\n'
            f'时间: {ts}\n'
            f'阶段概括: {n.get("summary", "")[:120]}\n'
            f'当时感受: {n.get("reflection", "")[:120]}\n'
            f'分享建议: {n.get("share_message", "")[:120]}'
        )

    return (
        header
        + "\n".join(body_lines)
        + "\n\n"
        + (
            '请根据以上内部信息，用当前人格自然回应用户。'
            '不要输出「时间」「阶段概括」「感受」「分享建议」等字段名，'
            '不要写成报告。'
            '只提及工具实际返回的内容，不要编造未读到的情节。'
        )
    )


def _fmt_status_tool_context(session: dict) -> str:
    """给 autoread_get_status LLM Tool 使用的内部上下文。"""
    title = session.get("current_book_title", "?")
    idx = session.get("current_chunk_index", 0)
    total = session.get("total_chunks", 0)
    paused = session.get("paused", False)
    last = session.get("last_read_at")
    nxt = session.get("next_read_at")

    if idx >= total > 0:
        status_line = "已读完整本书"
    elif paused:
        status_line = "阅读已暂停"
    else:
        status_line = "正在持续阅读中"

    return (
        f'[内部阅读状态, 请不要原样复述字段名, 用当前人格自然回应]\n'
        f'书名: {title}\n'
        f'当前进度: 第 {idx}/{total} 段\n'
        f'状态: {status_line}\n'
        f'上次阅读时间: {last or "尚未阅读"}\n'
        f'下次计划阅读时间: {nxt or "待定"}\n'
        f'\n'
        f'请根据以上信息，用当前人格自然告知用户当前阅读状态。'
        f'不要输出「书名」「进度」「状态」「上次阅读时间」「下次阅读时间」等字段名。'
    )


class ReadStepResult:
    """一次阅读推进的统一结果对象。

    不同入口使用不同字段:
    - /read step -> debug_message
    - LLM Tool -> tool_context
    - Worker share -> share_message
    """

    __slots__ = ("record", "debug_message", "tool_context", "share_message", "advanced", "error")

    def __init__(
        self,
        *,
        record: dict | None = None,
        debug_message: str = "",
        tool_context: str = "",
        share_message: str = "",
        advanced: bool = False,
        error: str | None = None,
    ):
        self.record = record or {}
        self.debug_message = debug_message
        self.tool_context = tool_context
        self.share_message = share_message
        self.advanced = advanced
        self.error = error


def _should_share_record(record: dict, share_mode: str) -> bool:
    """根据 ReadingRecord 和 auto_share_mode 决定是否应主动分享。

    使用新 schema 的 importance_score + share_message 替代旧的 should_share。
    """
    share_msg = record.get("share_message", "")
    if not share_msg.strip():
        return False

    if share_mode == "every_step":
        return True
    if share_mode == "none":
        return False

    importance = float(record.get("importance_score", 0.0))
    needs_review = bool(record.get("needs_deeper_review", False))

    if share_mode == "chapter":
        # 重要性高或需要深入复核时分享
        return importance >= 0.5 or needs_review or bool(share_msg.strip())

    if share_mode in ("daily", "finish"):
        return importance >= 0.5 or needs_review

    return False


class AutoReadService:
    """阅读业务编排层。"""

    def __init__(
        self,
        *,
        context,
        config_service,
        data_dir: Path,
        state_store,
        book_loader,
        chunker,
        note_writer,
        memory_bridge,
    ):
        self.context = context
        self.config_service = config_service
        self.data_dir = data_dir
        self.state_store = state_store
        self.book_loader = book_loader
        self.chunker = chunker
        self.note_writer = note_writer
        self.memory_bridge = memory_bridge

    def _enabled_message(self) -> str | None:
        """Return a disabled message when the global reading switch is off.

        Returns:
            A user-visible disabled message, or None when reading is enabled.
        """
        if not self.config_service.get("enabled", True):
            return "阅读功能当前已关闭。请先在插件设置中启用阅读。"
        return None

    # ------------------------------------------------------------------
    # bind
    # ------------------------------------------------------------------

    async def bind(self, umo: str) -> str:
        """绑定当前会话，用于后续主动分享。"""
        if disabled := self._enabled_message():
            return disabled
        session = await self.state_store.bind_session(umo)
        return (
            f"已绑定当前会话。\n"
            f"会话标识: {umo}\n"
            f"绑定时间: {session.get('bound_at', 'unknown')}"
        )

    # ------------------------------------------------------------------
    # import_book
    # ------------------------------------------------------------------

    async def import_book(self, filename: str) -> str:
        """导入本地书籍。"""
        if disabled := self._enabled_message():
            return disabled
        try:
            imported = await self.book_loader.import_local_book(filename)
        except (ValueError, FileNotFoundError) as exc:
            return f"导入失败: {exc}"

        meta = imported.meta
        book_id = meta["book_id"]

        # 切片
        chunks = self.chunker.split(imported.text)
        meta["total_chunks"] = len(chunks)

        # 保存 chunks
        chunks_path = self.data_dir / "chunks" / f"{book_id}.chunks.json"
        await self.chunker.save_chunks(chunks_path, chunks)

        # 注册到 state
        await self.state_store.register_book(meta)

        return (
            f"已导入《{meta['title']}》。\n"
            f"book_id: {book_id}\n"
            f"总字符数: {meta['total_chars']}\n"
            f"切片数: {meta['total_chunks']}\n"
            f"每段约 {self.config_service.get('chunk_size', 1800)} 字"
        )

    # ------------------------------------------------------------------
    # list_books
    # ------------------------------------------------------------------

    async def list_books(self) -> str:
        """列出已导入书籍。"""
        if disabled := self._enabled_message():
            return disabled
        books = await self.state_store.list_books()
        if not books:
            return (
                "暂无已导入的书籍。"
                "请先将 txt/md 文件放入 plugin_data/astrbot_plugin_autoread/books/ "
                "后使用 /read import <文件名> 导入。"
            )

        # P1-3: 使用 format_book_list_item 优先展示 display_name/author
        from .book_metadata import format_book_list_item, normalize_book_meta

        lines = ["已导入书籍:"]
        for b in books:
            normalize_book_meta(b)  # 懒补全旧数据
            lines.append(format_book_list_item(b))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # choose_book
    # ------------------------------------------------------------------

    async def choose_book(self, umo: str, preference: str = "") -> str:
        """根据偏好选择书籍（仅返回建议，不自动开始阅读）。"""
        if disabled := self._enabled_message():
            return disabled
        books = await self.state_store.list_books()
        if not books:
            return "暂无已导入的书籍可供选择。"

        if not preference:
            chosen = books[0]
            return (
                f"当前没有特别的偏好，建议阅读《{chosen['title']}》。\n"
                f"book_id: {chosen['book_id']}\n"
                f"如需开始阅读，请调用 autoread_start_book 或使用 /read start {chosen['book_id']}"
            )

        pref_lower = preference.lower()
        scored = []
        for b in books:
            title_lower = b["title"].lower()
            score = 0
            for word in pref_lower.split():
                if word in title_lower:
                    score += 10
            if pref_lower in title_lower:
                score += 20
            scored.append((score, b))

        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = scored[0][1]

        return (
            f"根据偏好「{preference}」，建议阅读《{chosen['title']}》。\n"
            f"book_id: {chosen['book_id']}\n"
            f"如需开始阅读，请调用 autoread_start_book 或使用 /read start {chosen['book_id']}"
        )

    # ------------------------------------------------------------------
    # start_book
    # ------------------------------------------------------------------

    async def start_book(
        self,
        umo: str,
        book_id: str,
        interval_minutes: int | float | None = None,
    ) -> str:
        """开始持续阅读一本书。"""
        if disabled := self._enabled_message():
            return disabled
        book = await self.state_store.get_book(book_id)
        if book is None:
            return f"未找到书籍 {book_id}。请先导入或使用 /read list 查看可用书籍。"

        if interval_minutes is None:
            interval_minutes = int(self.config_service.get("default_interval_minutes", 1440))
        else:
            interval_minutes = int(interval_minutes)

        auto_share_mode = self.config_service.get("auto_share_mode", "chapter")

        session = await self.state_store.start_book(
            umo=umo,
            book_id=book_id,
            title=book["title"],
            total_chunks=book["total_chunks"],
            interval_minutes=interval_minutes,
            auto_share_mode=auto_share_mode,
        )

        return (
            f"已开始阅读《{book['title']}》。\n"
            f"总段数: {book['total_chunks']}\n"
            f"当前进度: 第 {session['current_chunk_index']}/{session['total_chunks']} 段\n"
            f"阅读间隔: {interval_minutes} 分钟\n"
            f"分享模式: {auto_share_mode}"
        )

    # ------------------------------------------------------------------
    # read_next_chunk
    # ------------------------------------------------------------------

    async def read_next_chunk(
        self,
        umo: str,
        reason: str = "",
        send_message: bool = False,
        source: str = "command",
    ) -> str:
        """读取当前书的下一段文本、生成笔记、推进进度。

        硬性规则:
        - LLM 调用失败: 不推进进度
        - note 保存失败: 不推进进度
        - 主动发送失败: 不回滚进度，不丢失笔记，只记录 last_error

        返回格式由 source 决定:
        - "command" -> _fmt_command_result (结构化调试信息)
        - "llm_tool" -> _fmt_tool_context (内部上下文 + 自然回应指令)
        - "worker" / 其他 -> _fmt_share_message (自然语言分享)
        """
        if disabled := self._enabled_message():
            return disabled
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。请先使用 /read bind 绑定。"
        if not session.get("current_book_id"):
            return "当前没有正在阅读的书。请先使用 /read start <book_id> 开始阅读。"

        book_id = session["current_book_id"]
        book = await self.state_store.get_book(book_id)
        if book is None:
            return f"书籍 {book_id} 的 meta 信息丢失。"

        chunk_index = session.get("current_chunk_index", 0)
        total_chunks = session.get("total_chunks", 0)

        if chunk_index >= total_chunks:
            return (
                f"已读完整本《{book['title']}》！\n"
                f"进度: {chunk_index}/{total_chunks}\n"
                f"可使用 /read notes 回顾笔记。"
            )

        # 加载当前 chunk
        chunks_path = self.data_dir / "chunks" / f"{book_id}.chunks.json"
        if not chunks_path.exists():
            return f"切片文件丢失: {chunks_path}"

        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        if chunk_index >= len(chunks):
            return f"chunk index {chunk_index} 超出范围 (total={len(chunks)})"

        chunk = chunks[chunk_index]

        # 调用 LLM 生成笔记
        try:
            note = await self.note_writer.write_note(
                umo=umo,
                book_id=book_id,
                book_title=book["title"],
                chunk=chunk,
                chunk_index=chunk_index,
                chunk_total=total_chunks,
            )
        except Exception as exc:
            await self.state_store.set_last_error(umo, f"LLM 调用失败: {exc}")
            logger.error(f"[AutoRead] LLM failed for chunk {chunk_index}: {exc}")
            return f"阅读笔记生成失败: {exc}\n进度未推进，当前仍在第 {chunk_index}/{total_chunks} 段。"

        # 保存笔记（失败不推进进度）
        try:
            await self.state_store.append_note(book_id, note)
        except Exception as exc:
            await self.state_store.set_last_error(umo, f"笔记保存失败: {exc}")
            logger.error(f"[AutoRead] Failed to save note: {exc}")
            return f"笔记保存失败: {exc}\n进度未推进，当前仍在第 {chunk_index}/{total_chunks} 段。"

        # 写入长期记忆（失败不阻塞）
        try:
            await self.memory_bridge.write_memory(note)
        except Exception as exc:
            logger.warning(f"[AutoRead] memory_bridge failed (non-blocking): {exc}")

        # 推进进度
        await self.state_store.advance_progress(umo)

        # 根据入口类型构建不同的返回文本
        if source == "llm_tool":
            result = _fmt_tool_context(
                book_title=book["title"],
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                chapter=chunk.get("chapter", "未知"),
                note=note,
            )
        elif source == "command":
            result = _fmt_command_result(
                book_title=book["title"],
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                chapter=chunk.get("chapter", "未知"),
                note=note,
            )
        else:
            # worker 或其他: 优先使用 share_message
            result = _fmt_share_message(
                book_title=book["title"],
                chunk_index=chunk_index + 1,
                total_chunks=total_chunks,
                note=note,
            )

        # 主动分享（仅在 worker 模式下执行）
        if send_message and source == "worker":
            share_result = await self._maybe_share(umo, session, note)
            if share_result:
                result += "\n\n" + share_result

        return result

    # ------------------------------------------------------------------
    # get_status
    # ------------------------------------------------------------------

    async def get_status(self, umo: str, source: str = "command") -> str:
        """返回当前阅读状态。

        source="llm_tool" 时返回内部上下文（带自然回应指令）。
        source="command" 时返回结构化调试信息。
        """
        if disabled := self._enabled_message():
            return disabled
        session = await self.state_store.get_session(umo)
        if session is None or not session.get("current_book_id"):
            msg = (
                "当前没有进行中的阅读任务。\n"
                "使用 /read bind 绑定会话，/read import 导入书籍，/read start <book_id> 开始阅读。"
            )
            if source == "llm_tool":
                return (
                    f"[内部状态查询结果]\n{msg}\n"
                    f"请自然告知用户当前没有进行中的阅读任务。"
                )
            return msg

        if source == "llm_tool":
            return _fmt_status_tool_context(session)

        # command 入口: 结构化
        if session.get("current_chunk_index", 0) >= session.get("total_chunks", 0):
            status = "DONE 已读完"
        elif session.get("paused"):
            status = "PAUSED 已暂停"
        else:
            status = "READING 阅读中"

        return (
            f"{status}\n"
            f"书名: 《{session.get('current_book_title', '?')}》\n"
            f"进度: {session.get('current_chunk_index', 0)}/{session.get('total_chunks', 0)} 段\n"
            f"间隔: {session.get('reading_interval_minutes', '?')} 分钟\n"
            f"分享模式: {session.get('auto_share_mode', 'chapter')}\n"
            f"上次阅读: {session.get('last_read_at') or '尚未阅读'}\n"
            f"下次阅读: {session.get('next_read_at') or '待定'}\n"
            + (f"最后错误: {session['last_error']}" if session.get("last_error") else "")
        )

    # ------------------------------------------------------------------
    # get_notes
    # ------------------------------------------------------------------

    async def get_notes(self, umo: str, limit: int = 5, source: str = "command") -> str:
        """返回最近笔记。

        source="llm_tool" 时返回内部上下文（带自然回应指令）。
        source="command" 时返回结构化调试信息。
        """
        if disabled := self._enabled_message():
            return disabled
        notes = await self.state_store.get_recent_notes_for_session(umo, limit)
        if not notes:
            msg = "暂无阅读笔记。"
            if source == "llm_tool":
                return (
                    f"[内部阅读笔记查询结果]\n{msg}\n"
                    f"请自然告知用户还没有笔记，不要编造。"
                )
            return msg

        if source == "llm_tool":
            session = await self.state_store.get_session(umo)
            book_title = ""
            if session:
                book_title = session.get("current_book_title", "")
            return _fmt_notes_tool_context(notes, limit, book_title)

        # command 入口: 结构化
        lines = ["最近阅读笔记:"]
        for n in notes:
            ts = n.get("created_at", "?")
            chunk_idx = n.get("chunk_index", "?")
            chapter = n.get("chapter", "?")
            summary = n.get("summary", "")[:100]
            reflection = n.get("reflection", "")[:100]
            lines.append(
                f"---\n"
                f"[{ts}] 第{chunk_idx}段 ({chapter})\n"
                f"摘要: {summary}\n"
                f"感想: {reflection}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # pause / resume / stop
    # ------------------------------------------------------------------

    async def pause(self, umo: str) -> str:
        if disabled := self._enabled_message():
            return disabled
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。"
        if not session.get("current_book_id"):
            return "当前没有正在阅读的书。"
        await self.state_store.update_session(umo, {"paused": True})
        return f"已暂停《{session.get('current_book_title', '?')}》的后台阅读。"

    async def resume(self, umo: str) -> str:
        if disabled := self._enabled_message():
            return disabled
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。"
        if not session.get("current_book_id"):
            return "当前没有正在阅读的书。"
        await self.state_store.update_session(umo, {"paused": False})
        return f"已恢复《{session.get('current_book_title', '?')}》的后台阅读。"

    async def stop(self, umo: str) -> str:
        if disabled := self._enabled_message():
            return disabled
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。"
        title = session.get("current_book_title", "?")
        await self.state_store.stop_session(umo)
        return f"已停止《{title}》的阅读任务。历史笔记已保留。"

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _maybe_share(
        self, umo: str, session: dict, record: dict
    ) -> str | None:
        """根据 auto_share_mode 和 ReadingRecord 决定是否主动分享，并尝试发送消息。"""
        share_mode = session.get("auto_share_mode", "chapter")

        if not _should_share_record(record, share_mode):
            return None

        share_msg = record.get("share_message", "") or record.get("summary", "")
        title = session.get("current_book_title", "?")
        idx = session.get("current_chunk_index", 0)
        total = session.get("total_chunks", 0)

        message = (
            f"我刚刚继续读了一小段《{title}》。\n\n"
            f"{share_msg}\n\n"
            f"现在进度: {idx}/{total}"
        )

        try:
            from astrbot.api.event import MessageChain
            await self.context.send_message(umo, MessageChain().message(message))
            return None
        except Exception as exc:
            err_msg = f"主动消息发送失败: {exc}"
            await self.state_store.set_last_error(umo, err_msg)
            logger.warning(f"[AutoRead] Share failed: {exc}")
            return f"(主动分享失败: {err_msg}。笔记已保存，可通过 /read notes 查看。)"

    # ------------------------------------------------------------------
    # 重新阅读（不推进主进度）
    # ------------------------------------------------------------------

    async def reread_range(
        self,
        umo: str,
        book_id: str,
        start_index: int | None = None,
        end_index: int | None = None,
        start_percent: float | None = None,
        end_percent: float | None = None,
        note_id: str | None = None,
        source: str = "command",
    ) -> str:
        """重新阅读指定范围。不推进主进度，不删除旧笔记。"""
        if disabled := self._enabled_message():
            return disabled

        book = await self.state_store.get_book(book_id)
        if book is None:
            return f"书籍 {book_id} 不存在。"

        total_chunks = book.get("total_chunks", 0)
        if total_chunks == 0:
            return f"《{book['title']}》尚未切片，无法重读。"

        chunks_path = self.data_dir / "chunks" / f"{book_id}.chunks.json"
        if not chunks_path.exists():
            return f"切片文件丢失: {chunks_path}"

        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))

        # 确定范围
        if note_id:
            found = False
            notes, _ = await self.state_store.get_all_notes(book_id=book_id, page=1, page_size=10000)
            for raw in notes:
                rid = raw.get("record_id") or raw.get("note_id", "")
                if rid == note_id:
                    idx = raw.get("chunk_index", -1)
                    if idx >= 0 and idx < len(chunks):
                        start_index = idx
                        end_index = idx
                        found = True
                    break
            if not found:
                return (
                    f"未找到笔记 {note_id}，或该笔记没有保存原文范围。\n"
                    f"请改用 --book <book_id> --from <start> --to <end> 指定范围。"
                )

        if start_percent is not None:
            start_index = int(start_percent / 100 * total_chunks)
        if end_percent is not None:
            end_index = int(end_percent / 100 * total_chunks)

        if start_index is None:
            return "请指定重读范围: --from <index> 或 --from <percent>%"

        if end_index is None:
            end_index = start_index

        start_index = max(0, min(start_index, len(chunks) - 1))
        end_index = max(start_index, min(end_index, len(chunks) - 1))

        logger.info(
            f"[AutoRead] reread: book={book_id} range=[{start_index},{end_index}] "
            f"total={total_chunks} note={note_id or 'N/A'}"
        )

        results = []
        for idx in range(start_index, end_index + 1):
            chunk = chunks[idx]
            try:
                note = await self.note_writer.write_note(
                    umo=umo,
                    book_id=book_id,
                    book_title=book["title"],
                    chunk=chunk,
                    chunk_index=idx,
                    chunk_total=total_chunks,
                )
            except Exception as exc:
                results.append(f"第 {idx + 1} 段笔记生成失败: {exc}")
                continue

            note["source_stage"] = "reread"
            try:
                await self.state_store.append_note(book_id, note)
            except Exception as exc:
                results.append(f"第 {idx + 1} 段笔记保存失败: {exc}")
                continue

            if source == "llm_tool":
                results.append(
                    _fmt_tool_context(book["title"], idx, total_chunks, chunk.get("chapter", "?"), note)
                )
            else:
                results.append(
                    _fmt_command_result(book["title"], idx, total_chunks, chunk.get("chapter", "?"), note)
                )

        if not results:
            return "重新阅读未生成任何笔记。"

        summary = (
            f"[REREAD] 《{book['title']}》第 {start_index + 1}-{end_index + 1}/{total_chunks} 段\n"
            f"主进度未改变。共生成 {len(results)} 条笔记。\n\n"
        )
        return summary + "\n\n".join(results)

    # ------------------------------------------------------------------
    # 设置阅读进度（不读取内容）
    # ------------------------------------------------------------------

    async def set_progress(
        self,
        umo: str,
        book_id: str,
        chunk_index: int | None = None,
        percent: float | None = None,
    ) -> str:
        """设置主阅读进度。不读取内容，不创建笔记。"""
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。请先使用 /read bind 绑定。"

        if session.get("current_book_id") and session["current_book_id"] != book_id:
            return (
                f"当前正在阅读《{session.get('current_book_title', '?')}》"
                f"（{session['current_book_id']}）。\n"
                f"请先 /read stop 停止当前阅读，再设置新书的进度。"
            )

        book = await self.state_store.get_book(book_id)
        if book is None:
            return f"书籍 {book_id} 不存在。"

        total_chunks = book.get("total_chunks", 0)
        if total_chunks == 0:
            return f"《{book['title']}》尚未切片，无法设置进度。"

        if percent is not None:
            chunk_index = int(percent / 100 * total_chunks)

        if chunk_index is None:
            return "请指定目标进度: --percent <N>% 或 --index <N>"

        chunk_index = max(0, min(chunk_index, total_chunks - 1))

        if not session.get("current_book_id"):
            session["current_book_id"] = book_id
            session["current_book_title"] = book["title"]
            session["total_chunks"] = total_chunks

        session["current_chunk_index"] = chunk_index
        await self.state_store.update_session(umo, {
            "current_chunk_index": chunk_index,
            "current_book_id": book_id,
            "current_book_title": book["title"],
            "total_chunks": total_chunks,
        })

        logger.info(f"[AutoRead] set_progress: book={book_id} chunk={chunk_index}/{total_chunks}")
        return (
            f"[PROGRESS] 《{book['title']}》进度已设为第 {chunk_index + 1}/{total_chunks} 段"
            f"（{round(chunk_index / total_chunks * 100, 1)}%）。\n"
            f"未读取内容，未生成笔记。下次继续阅读将从此处开始。"
        )
