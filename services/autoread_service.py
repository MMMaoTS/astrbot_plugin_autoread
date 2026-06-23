"""AutoRead 业务编排核心。

命令入口、LLM Tool 入口、后台 worker 都必须复用本层，不得重复实现阅读逻辑。
"""

import json
from pathlib import Path

from astrbot.api import logger


class AutoReadService:
    """阅读业务编排层。"""

    def __init__(
        self,
        *,
        context,
        config,
        data_dir: Path,
        state_store,
        book_loader,
        chunker,
        note_writer,
        memory_bridge,
    ):
        self.context = context
        self.config = config
        self.data_dir = data_dir
        self.state_store = state_store
        self.book_loader = book_loader
        self.chunker = chunker
        self.note_writer = note_writer
        self.memory_bridge = memory_bridge

    # ------------------------------------------------------------------
    # bind
    # ------------------------------------------------------------------

    async def bind(self, umo: str) -> str:
        """绑定当前会话，用于后续主动分享。"""
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
            f"每段约 {self.config.get('chunk_size', 1800)} 字"
        )

    # ------------------------------------------------------------------
    # list_books
    # ------------------------------------------------------------------

    async def list_books(self) -> str:
        """列出已导入书籍。"""
        books = await self.state_store.list_books()
        if not books:
            return "暂无已导入的书籍。请先将 txt/md 文件放入 plugin_data/astrbot_plugin_autoread/books/ 后使用 /read import <文件名> 导入。"

        lines = ["已导入书籍:"]
        for b in books:
            lines.append(
                f"  [{b['book_id']}] 《{b['title']}》 "
                f"— {b.get('total_chunks', '?')} 段 "
                f"({b.get('source_type', 'unknown')})"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # choose_book
    # ------------------------------------------------------------------

    async def choose_book(self, umo: str, preference: str = "") -> str:
        """根据偏好选择书籍（仅返回建议，不自动开始阅读）。"""
        books = await self.state_store.list_books()
        if not books:
            return "暂无已导入的书籍可供选择。"

        if not preference:
            # 无偏好时返回第一本
            chosen = books[0]
            return (
                f"当前没有特别的偏好，建议阅读《{chosen['title']}》。\n"
                f"book_id: {chosen['book_id']}\n"
                f"如需开始阅读，请调用 autoread_start_book 或使用 /read start {chosen['book_id']}"
            )

        # 简单关键词匹配
        pref_lower = preference.lower()
        scored = []
        for b in books:
            title_lower = b["title"].lower()
            score = 0
            # 标题包含偏好词
            for word in pref_lower.split():
                if word in title_lower:
                    score += 10
            # 加上一些基本的启发式匹配
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
        book = await self.state_store.get_book(book_id)
        if book is None:
            return f"未找到书籍 {book_id}。请先导入或使用 /read list 查看可用书籍。"

        if interval_minutes is None:
            interval_minutes = int(self.config.get("default_interval_minutes", 1440))
        else:
            interval_minutes = int(interval_minutes)

        auto_share_mode = self.config.get("auto_share_mode", "chapter")

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

        硬性规则：
        - LLM 调用失败：不推进进度
        - note 保存失败：不推进进度
        - 主动发送失败：不回滚进度，不丢失笔记，只记录 last_error
        """
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
                total_chunks=total_chunks,
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
        new_session = await self.state_store.advance_progress(umo)

        # 构建返回消息
        result_lines = [
            f"📖 《{book['title']}》第 {chunk_index + 1}/{total_chunks} 段",
            f"章节: {chunk.get('chapter', '未知')}",
            f"",
            f"摘要: {note.get('summary', '')}",
        ]
        if note.get("detail"):
            result_lines.append(f"细节: {note.get('detail', '')}")
        if note.get("reflection"):
            result_lines.append(f"感想: {note.get('reflection', '')}")

        result = "\n".join(result_lines)

        # 主动分享（如果配置了且需要）
        if send_message and source == "worker":
            share_result = await self._maybe_share(umo, session, note)
            if share_result:
                result += "\n\n" + share_result

        return result

    # ------------------------------------------------------------------
    # get_status
    # ------------------------------------------------------------------

    async def get_status(self, umo: str) -> str:
        """返回当前阅读状态。"""
        session = await self.state_store.get_session(umo)
        if session is None or not session.get("current_book_id"):
            return (
                "当前没有进行中的阅读任务。\n"
                "使用 /read bind 绑定会话，/read import 导入书籍，/read start <book_id> 开始阅读。"
            )

        if session.get("current_chunk_index", 0) >= session.get("total_chunks", 0):
            status = "✅ 已读完"
        elif session.get("paused"):
            status = "⏸️ 已暂停"
        else:
            status = "📖 阅读中"

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

    async def get_notes(self, umo: str, limit: int = 5) -> str:
        """返回最近笔记。"""
        notes = await self.state_store.get_recent_notes_for_session(umo, limit)
        if not notes:
            return "暂无阅读笔记。"

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
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。"
        if not session.get("current_book_id"):
            return "当前没有正在阅读的书。"
        await self.state_store.update_session(umo, {"paused": True})
        return f"已暂停《{session.get('current_book_title', '?')}》的后台阅读。"

    async def resume(self, umo: str) -> str:
        session = await self.state_store.get_session(umo)
        if session is None:
            return "当前会话尚未绑定。"
        if not session.get("current_book_id"):
            return "当前没有正在阅读的书。"
        await self.state_store.update_session(umo, {"paused": False})
        return f"已恢复《{session.get('current_book_title', '?')}》的后台阅读。"

    async def stop(self, umo: str) -> str:
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
        self, umo: str, session: dict, note: dict
    ) -> str | None:
        """根据 auto_share_mode 决定是否主动分享，并尝试发送消息。"""
        share_mode = session.get("auto_share_mode", "chapter")

        should_send = False
        if share_mode == "every_step":
            should_send = True
        elif share_mode == "chapter" and note.get("should_share"):
            should_send = True
        elif share_mode == "none":
            should_send = False
        # daily / finish 暂按 chapter 处理
        elif share_mode in ("daily", "finish"):
            should_send = note.get("should_share", False)

        if not should_send:
            return None

        share_msg = note.get("share_message", "") or note.get("summary", "")
        title = session.get("current_book_title", "?")
        idx = session.get("current_chunk_index", 0)
        total = session.get("total_chunks", 0)

        message = (
            f"我刚刚继续读了一小段《{title}》。\n\n"
            f"{share_msg}\n\n"
            f"现在进度：{idx}/{total}"
        )

        try:
            from astrbot.api.message_components import MessageChain
            await self.context.send_message(umo, MessageChain().message(message))
            return None  # 成功发送，不返回额外文本
        except Exception as exc:
            err_msg = f"主动消息发送失败: {exc}"
            await self.state_store.set_last_error(umo, err_msg)
            logger.warning(f"[AutoRead] Share failed: {exc}")
            return f"（主动分享失败: {err_msg}。笔记已保存，可通过 /read notes 查看。）"
