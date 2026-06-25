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
        f'刚读完《{book_title}》的一段内容。\n'
        f'进度: 第 {chunk_index + 1}/{total_chunks} 段\n'
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
    """给 LLM Tool 使用: 结构化阅读事实"""
    lines = [
        _fmt_tool_header(book_title, chunk_index, total_chunks),
        f'章节: {chapter or "未知"}',
        f'本段概括: {note.get("summary", "")}',
        f'注意到的细节: {note.get("detail", "") or "(无)"}',
        f'角色感受/疑问: {note.get("reflection", "") or "(无)"}',
        f'可参考的分享素材: {note.get("share_message", "") or note.get("summary", "")}',
    ]
    return "\n".join(lines)


def _fmt_share_message(
    book_title: str,
    chunk_index: int,
    total_chunks: int,
    note: dict,
) -> str:
    """给后台主动分享使用: 优先使用 share_message."""
    share_msg = note.get("share_message", "") or note.get("summary", "")
    return (
        f'我刚刚继续读了一小段《{book_title}》。\n\n'
        f'{share_msg}\n\n'
        f'(进度: 第 {chunk_index}/{total_chunks} 段)'
    )


def _fmt_notes_tool_context(notes: list[dict], book_title: str = "") -> str:
    """给 autoread_get_notes LLM Tool 使用的用户可见安全事实摘要。

    返回内容即使被模型原样输出，也读起来像角色在回顾自己的笔记，
    而非系统调试信息。不含时间戳、编号、内部字段。
    """
    if not notes:
        return "目前还没有保存的阅读笔记。"

    book_ref = f"《{book_title}》" if book_title else "当前正在读的书"

    # ----- 单条笔记：自然段落 -----
    if len(notes) == 1:
        n = notes[0]
        summary = n.get("summary", "").strip()
        detail = n.get("detail", "").strip()
        reflection = n.get("reflection", "").strip()

        if not summary and not detail and not reflection:
            return f"关于{book_ref}有一条阅读笔记，但内容为空。"

        lines = [f"关于{book_ref}，有一条阅读笔记。"]
        if summary:
            lines.append(f"读到的内容概括：{summary}")
        if detail:
            lines.append(f"其中注意到的细节：{detail}")
        if reflection:
            lines.append(f"我当时的感受：{reflection}")
        return "\n".join(lines)

    # ----- 多条笔记：自然概览 + 逐条 -----
    lines = [f"关于{book_ref}，目前保存了 {len(notes)} 条阅读笔记。"]
    lines.append("")

    for i, n in enumerate(notes, 1):
        summary = n.get("summary", "").strip()
        detail = n.get("detail", "").strip()
        reflection = n.get("reflection", "").strip()

        if not summary and not detail and not reflection:
            continue

        parts = []
        if summary:
            parts.append(f"这一段主要讲了：{summary}")
        if detail:
            parts.append(f"注意到的细节：{detail}")
        if reflection:
            parts.append(f"我的感受：{reflection}")

        if parts:
            if len(notes) <= 3:
                lines.append(f"第{i}条笔记——" + "；".join(parts))
            else:
                lines.append(f"· " + "；".join(parts))

    if len(lines) == 2:
        # 所有笔记都是空的
        return f"关于{book_ref}有 {len(notes)} 条阅读笔记，但内容均为空。"

    return "\n".join(lines)


def _fmt_status_tool_context(session: dict) -> str:
    """给 autoread_get_status LLM Tool 使用的阅读状态事实"""
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
        status_line = "正在阅读中"

    return (
        f'书名: 《{title}》\n'
        f'进度: 第 {idx}/{total} 段\n'
        f'状态: {status_line}\n'
        f'上次阅读: {last or "尚未阅读"}\n'
        f'下次计划: {nxt or "待定"}'
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

    async def list_books(self, source: str = "command") -> str:
        """列出已导入书籍。

        source="llm_tool" 时返回给 LLM 的事实摘要（适合角色自然表达）。
        source="command" 时返回结构化系统列表（/read list 兜底）。
        """
        if disabled := self._enabled_message():
            return disabled
        books = await self.state_store.list_books()

        from .book_metadata import normalize_book_meta, display_title, display_author

        for b in books:
            normalize_book_meta(b)

        if not books:
            if source == "llm_tool":
                return "书架里现在还没有能读的书。"
            return (
                "暂无已导入的书籍。"
                "请先将 txt/md 文件放入 plugin_data/astrbot_plugin_autoread/books/ "
                "后使用 /read import <文件名> 导入。"
            )

        if source == "llm_tool":
            # P1-4.1: 用户可见安全事实——即使被 LLM 原样输出也不会出戏
            book_count = len(books)
            if book_count == 1:
                b = books[0]
                dname = display_title(b)
                author = display_author(b)
                if author:
                    return f"书架里现在有《{dname}》，作者是{author}。"
                return f"书架里现在有《{dname}》。"
            # 多本书
            items = []
            for b in books:
                dname = display_title(b)
                author = display_author(b)
                if author:
                    items.append(f"《{dname}》（{author}）")
                else:
                    items.append(f"《{dname}》")
            return f"书架里现在有 {book_count} 本书：" + "、".join(items) + "。"

        # source="command": 系统列表（/read list 兜底）
        from .book_metadata import format_book_list_item
        lines = ["已导入书籍:"]
        for b in books:
            lines.append(format_book_list_item(b))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # choose_book
    # ------------------------------------------------------------------

    async def choose_book(self, umo: str, preference: str = "", source: str = "command") -> str:
        """根据偏好选择书籍（仅返回建议，不自动开始阅读）。

        source="llm_tool" 时返回给 LLM 的内部推荐上下文。
        source="command" 时返回结构化推荐信息（/read choose 兜底）。
        """
        if disabled := self._enabled_message():
            return disabled
        books = await self.state_store.list_books()
        if not books:
            return "暂无已导入的书籍可供选择。"

        if not preference:
            chosen = books[0]
            if source == "llm_tool":
                return (
                    f"书架上目前推荐《{chosen['title']}》"
                    f"（book_id: {chosen['book_id']}，共 {chosen.get('total_chunks', 0)} 段）。"
                    f"如果用户想开始读，需要调用 autoread_start_book(book_id=\"{chosen['book_id']}\")。"
                )
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

        if source == "llm_tool":
            return (
                f"根据偏好「{preference}」，书架上最匹配的是《{chosen['title']}》"
                f"（book_id: {chosen['book_id']}，共 {chosen.get('total_chunks', 0)} 段）。"
                f"如果用户想开始读，需要调用 autoread_start_book(book_id=\"{chosen['book_id']}\")。"
            )

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
        source: str = "command",
    ) -> str:
        """开始持续阅读一本书。

        source="llm_tool" 时返回给 LLM 的内部上下文（强调"已就绪但尚未读取内容"）。
        source="command" 时返回结构化确认信息（/read start 兜底）。
        """
        if disabled := self._enabled_message():
            return disabled
        book = await self.state_store.get_book(book_id)
        if book is None:
            if source == "llm_tool":
                return f"没有找到对应的书，可能还没有导入。"
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

        if source == "llm_tool":
            # LLM Tool 上下文：强调"已就绪但未读"，引导模型继续调用 read_next
            return (
                f"《{book['title']}》的阅读会话已就绪"
                f"（共 {book['total_chunks']} 段，当前在第 0 段，尚未读取任何内容）。"
                f"用户想要读内容的话，需要继续调用 autoread_read_next 获取第一段。"
            )

        # command 入口：结构化确认（/read start 兜底）
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
            if source == "llm_tool":
                return "当前没有进行中的阅读会话，还没有开始读任何书。"
            return "当前会话尚未绑定。请先使用 /read bind 绑定。"
        if not session.get("current_book_id"):
            if source == "llm_tool":
                return "当前没有正在读的书。可以先搜索书架（autoread_search_books）找到想读的书，然后用 autoread_start_book 开始阅读。"
            return "当前没有正在阅读的书。请先使用 /read start <book_id> 开始阅读。"

        book_id = session["current_book_id"]
        book = await self.state_store.get_book(book_id)
        if book is None:
            return f"书籍 {book_id} 的 meta 信息丢失。"

        chunk_index = session.get("current_chunk_index", 0)
        total_chunks = session.get("total_chunks", 0)

        if chunk_index >= total_chunks:
            if source == "llm_tool":
                return (
                    f"《{book['title']}》已经全部读完啦"
                    f"（进度: {chunk_index}/{total_chunks}）。"
                )
            return (
                f"已读完整本《{book['title']}》！\n"
                f"进度: {chunk_index}/{total_chunks}\n"
                f"可使用 /read notes 回顾笔记。"
            )

        # 加载当前 chunk
        chunks_path = self.data_dir / "chunks" / f"{book_id}.chunks.json"
        if not chunks_path.exists():
            if source == "llm_tool":
                return f"《{book['title']}》的文本数据好像丢失了，暂时没法继续读。"
            return f"切片文件丢失: {chunks_path}"

        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        if chunk_index >= len(chunks):
            if source == "llm_tool":
                return f"《{book['title']}》的阅读进度似乎出了问题，当前进度超出了书籍范围。"
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
            if source == "llm_tool":
                return "目前还没有在读书。书架上的书都在，想读哪本或者想看看书架，告诉我就好。"
            msg = (
                "当前没有进行中的阅读任务。\n"
                "使用 /read bind 绑定会话，/read import 导入书籍，/read start <book_id> 开始阅读。"
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

    async def get_notes(
        self, umo: str, limit: int = 5, source: str = "command", book_id: str = "",
    ) -> str:
        """返回阅读笔记。

        source="llm_tool" 时返回用户可见安全的事实摘要。
        source="command" 时返回结构化调试信息（/read notes 兜底）。

        book_id 非空时查询指定书籍的笔记；为空时查询当前会话书的笔记。
        """
        if disabled := self._enabled_message():
            return disabled

        # 解析要查询的 book_id
        effective_book_id = book_id.strip() if book_id else ""
        book_title = ""

        if effective_book_id:
            # 指定书籍：获取元数据用于展示
            book = await self.state_store.get_book(effective_book_id)
            if book is None:
                if source == "llm_tool":
                    return f"没有找到对应的书籍。"
                return f"书籍 {effective_book_id} 不存在。"
            from .book_metadata import display_title
            book_title = display_title(book)
            notes, _ = await self.state_store.get_notes_by_book(
                effective_book_id, page=1, page_size=limit,
            )
        else:
            # 当前会话书
            notes = await self.state_store.get_recent_notes_for_session(umo, limit)
            if notes and source == "llm_tool":
                session = await self.state_store.get_session(umo)
                if session:
                    book_title = session.get("current_book_title", "")
                    # 尝试用 book metadata 取更好的 display_name
                    bid = session.get("current_book_id", "")
                    if bid:
                        book = await self.state_store.get_book(bid)
                        if book:
                            from .book_metadata import display_title
                            book_title = display_title(book)

        # 无笔记
        if not notes:
            if source == "llm_tool":
                if effective_book_id and book_title:
                    return f"关于《{book_title}》，目前还没有保存的阅读笔记。"
                return "目前还没有保存的阅读笔记。"
            return "暂无阅读笔记。"

        # llm_tool：用户可见安全事实摘要
        if source == "llm_tool":
            return _fmt_notes_tool_context(notes, book_title)

        # command 入口：结构化（/read notes 兜底）
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
