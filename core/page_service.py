"""WebUI 专用业务编排层。

为 WebUI 前端提供聚合数据，组合 AutoReadService、ReadingStateStore 的结果。
不做 JSON 文件直接读写，不修改 notes 内容。
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from astrbot.api import logger
from ..models.reading_record import normalize_record


# book_id / note_id 安全校验正则
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# session_id 脱敏：取 umo 的 sha256 前 8 位
def _mask_session(umo: str) -> str:
    h = hashlib.sha256(umo.encode()).hexdigest()[:8]
    return f"s_{h}"


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


class WebUIService:
    """WebUI 管理页面的业务编排层。

    所有数据聚合在此完成，不直接写 state.json / notes.jsonl。
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        state_store,
        autoread_service,
        book_loader,
        chunker,
        config_service,
        provider_resolver=None,
        backup_service=None,
    ):
        self.data_dir = data_dir
        self.state_store = state_store
        self.autoread_service = autoread_service
        self.book_loader = book_loader
        self.chunker = chunker
        self.config_service = config_service
        self.provider_resolver = provider_resolver
        self.backup_service = backup_service

    # ------------------------------------------------------------------
    # 安全校验
    # ------------------------------------------------------------------

    @staticmethod
    def validate_book_id(book_id: str) -> bool:
        return bool(_SAFE_ID_RE.fullmatch(book_id))

    @staticmethod
    def validate_note_id(note_id: str) -> bool:
        return bool(_SAFE_ID_RE.fullmatch(note_id))

    @staticmethod
    def validate_filename(filename: str) -> bool:
        """防止路径穿越。"""
        if not filename or ".." in filename or filename.startswith("/"):
            return False
        return True

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    async def get_overview(self) -> dict:
        books_count = await self.state_store.count_books()
        notes_count = await self.state_store.count_all_notes()
        sessions = await self.state_store.list_sessions()
        active_count = sum(
            1 for s in sessions.values()
            if s.get("enabled") and s.get("current_book_id") and not s.get("paused")
        )

        current_books = []
        for umo, s in sessions.items():
            if s.get("current_book_id"):
                current_books.append({
                    "session_id": _mask_session(umo),
                    "book_id": s.get("current_book_id", ""),
                    "title": s.get("current_book_title", "?"),
                    "current_chunk_index": s.get("current_chunk_index", 0),
                    "total_chunks": s.get("total_chunks", 0),
                    "paused": s.get("paused", False),
                })

        last_error = None
        last_error_at = None
        ttl_minutes = int(self.config_service.get("webui_last_error_ttl_minutes", 30))
        for s in sessions.values():
            if s.get("last_error"):
                err_at = s.get("last_error_at", "")
                if err_at and ttl_minutes > 0:
                    try:
                        err_dt = datetime.fromisoformat(err_at)
                        age = datetime.now(timezone(timedelta(hours=8))) - err_dt
                        if age.total_seconds() > ttl_minutes * 60:
                            continue  # 已过期，跳过
                    except (ValueError, TypeError):
                        pass
                last_error = s["last_error"]
                last_error_at = err_at or None
                break

        return {
            "books_count": books_count,
            "notes_count": notes_count,
            "active_sessions_count": active_count,
            "current_books": current_books,
            "last_error": last_error,
            "last_error_at": last_error_at,
            "last_error_ttl_minutes": ttl_minutes,
        }

    # ------------------------------------------------------------------
    # Books
    # ------------------------------------------------------------------

    async def list_books(
        self, query: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        query = (query or "").strip()[:100]

        books = await self.state_store.list_books()
        sessions = await self.state_store.list_sessions()

        # 按 title / book_id 过滤
        if query:
            q = query.lower()
            books = [
                b for b in books
                if q in b.get("title", "").lower() or q in b.get("book_id", "").lower()
            ]

        total = len(books)
        # 按创建时间倒序
        books.sort(key=lambda b: b.get("created_at", ""), reverse=True)

        start = (page - 1) * page_size
        end = start + page_size
        page_books = books[start:end]

        # 构建每本书的活跃信息
        items = []
        for b in page_books:
            bid = b["book_id"]
            active_sessions = [
                _mask_session(umo)
                for umo, s in sessions.items()
                if s.get("current_book_id") == bid
            ]
            notes_count = await self.state_store.count_notes_for_book(bid)

            # 找到最大进度
            max_progress = 0
            for s in sessions.values():
                if s.get("current_book_id") == bid:
                    max_progress = max(max_progress, s.get("current_chunk_index", 0))

            total_chunks = b.get("total_chunks", 0)
            percent = round(max_progress / total_chunks * 100, 2) if total_chunks > 0 else 0

            items.append({
                "book_id": bid,
                "title": b.get("title", ""),
                "source_type": b.get("source_type", "local"),
                "total_chars": b.get("total_chars", 0),
                "total_chunks": total_chunks,
                "created_at": b.get("created_at", ""),
                "is_active": len(active_sessions) > 0,
                "active_sessions": len(active_sessions),
                "notes_count": notes_count,
                "progress": {
                    "max_current_chunk_index": max_progress,
                    "total_chunks": total_chunks,
                    "percent": percent,
                },
            })

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def get_book_detail(self, book_id: str) -> dict:
        if not self.validate_book_id(book_id):
            return None
        book = await self.state_store.get_book(book_id)
        if book is None:
            return None

        sessions = await self.state_store.list_sessions()
        active_sessions = []
        for umo, s in sessions.items():
            if s.get("current_book_id") == book_id:
                active_sessions.append({
                    "session_id": _mask_session(umo),
                    "current_chunk_index": s.get("current_chunk_index", 0),
                    "total_chunks": s.get("total_chunks", 0),
                    "paused": s.get("paused", False),
                    "last_read_at": s.get("last_read_at"),
                    "next_read_at": s.get("next_read_at"),
                })

        notes_count = await self.state_store.count_notes_for_book(book_id)

        return {
            "book_id": book.get("book_id", ""),
            "title": book.get("title", ""),
            "source_type": book.get("source_type", "local"),
            "source_path": book.get("source_path", ""),
            "chunks_path": book.get("chunks_path", ""),
            "notes_path": book.get("notes_path", ""),
            "created_at": book.get("created_at", ""),
            "total_chars": book.get("total_chars", 0),
            "total_chunks": book.get("total_chunks", 0),
            "notes_count": notes_count,
            "active_sessions": active_sessions,
        }

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_book_file(self, upload) -> dict:
        """处理上传文件：校验、安全化文件名、落盘到 books/。"""
        if not self.config_service.get("webui_upload_enabled", True):
            raise PermissionError("WebUI 上传功能已关闭")

        filename = upload.filename or "unknown.txt"
        # 安全检查：只取 basename
        safe_name = Path(filename).name
        if not safe_name or safe_name.startswith("."):
            raise ValueError("无效的文件名")

        suffix = Path(safe_name).suffix.lower()
        allowed = [e.lower() for e in self.config_service.get("allowed_extensions", [".txt", ".md"])]
        if suffix not in allowed:
            raise ValueError(f"不支持的文件类型 {suffix}，仅支持: {', '.join(allowed)}")

        # 大小限制
        max_mb = int(self.config_service.get("webui_max_upload_mb", 10))
        max_bytes = max_mb * 1024 * 1024

        content = await upload.read()
        if len(content) > max_bytes:
            raise ValueError(f"文件超过大小限制 ({max_mb} MB)")

        # 生成存储文件名
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        short = uuid.uuid4().hex[:6]
        stored_name = f"upload_{ts}_{short}{suffix}"
        stored_path = self.data_dir / "books" / stored_name

        stored_path.write_bytes(content)

        logger.info(f"[AutoRead WebUI] Uploaded file: {safe_name} -> {stored_name} ({len(content)} bytes)")

        return {
            "filename": safe_name,
            "stored_filename": stored_name,
            "size": len(content),
            "message": "上传完成",
        }

    async def import_uploaded_book(
        self, stored_filename: str, title: str | None = None
    ) -> dict:
        """导入已上传的文件（调用现有 book_loader + chunker）。"""
        if not self.validate_filename(stored_filename):
            raise ValueError("无效的文件名")

        imported = await self.book_loader.import_local_book(stored_filename)
        meta = imported.meta
        book_id = meta["book_id"]

        if title:
            title = title.strip()[:120]
            if title:
                meta["title"] = title

        chunks = self.chunker.split(imported.text)
        meta["total_chunks"] = len(chunks)

        chunks_path = self.data_dir / "chunks" / f"{book_id}.chunks.json"
        await self.chunker.save_chunks(chunks_path, chunks)

        await self.state_store.register_book(meta)

        return {
            "book_id": book_id,
            "title": meta["title"],
            "total_chars": meta["total_chars"],
            "total_chunks": meta["total_chunks"],
        }

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def list_sessions(self) -> dict:
        sessions = await self.state_store.list_sessions()
        items = []
        for umo, s in sessions.items():
            if not s.get("current_book_id"):
                continue
            items.append({
                "session_id": _mask_session(umo),
                "book_id": s.get("current_book_id", ""),
                "title": s.get("current_book_title", "?"),
                "current_chunk_index": s.get("current_chunk_index", 0),
                "total_chunks": s.get("total_chunks", 0),
                "paused": s.get("paused", False),
                "last_read_at": s.get("last_read_at"),
                "next_read_at": s.get("next_read_at"),
            })
        return {"items": items}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def get_settings(self) -> dict:
        """返回当前有效配置（从 AstrBotConfig 读取）。"""
        settings = self.config_service.get_effective_config()
        return {
            "settings": settings,
            "source": "astrbot_config",
        }

    async def update_settings(self, patch: dict) -> dict:
        """更新配置 patch，直接写入 AstrBotConfig 并持久化。"""
        try:
            settings = await self.config_service.update_settings(patch)
            self.book_loader.allowed_extensions = list(
                self.config_service.get("allowed_extensions", [".txt", ".md"])
            )
            self.chunker.chunk_size = int(self.config_service.get("chunk_size", 1800))
            self.chunker.chunk_overlap = int(self.config_service.get("chunk_overlap", 120))
            if hasattr(self.autoread_service.memory_bridge, "backend"):
                self.autoread_service.memory_bridge.backend = self.config_service.get(
                    "memory_backend", "none"
                )
            return {
                "updated": True,
                "settings": settings,
                "message": "设置已保存",
            }
        except ValueError as exc:
            raise ValueError(str(exc))

    async def list_providers(self) -> dict:
        """列出可用 provider 列表（供 WebUI 设置页选择模型）。"""
        if self.provider_resolver is None:
            return {
                "items": [],
                "manual_input_supported": True,
                "message": "ProviderResolver 未初始化。请手动填写 provider_id。",
            }

        try:
            items = await self.provider_resolver.list_providers()
        except Exception as exc:
            logger.warning(f"[AutoRead WebUI] list_providers failed: {exc}")
            items = []

        return {
            "items": items,
            "manual_input_supported": True,
            "message": "" if items else "当前运行环境未提供可用模型列表接口，请手动填写 provider_id。",
        }

    # ------------------------------------------------------------------
    # Notes (只读)
    # ------------------------------------------------------------------

    async def get_notes(
        self,
        book_id: str = "",
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
    ) -> dict:
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        keyword = (keyword or "").strip()[:100]
        book_id = (book_id or "").strip()

        if book_id and not self.validate_book_id(book_id):
            raise ValueError("无效的 book_id")

        notes, total = await self.state_store.get_all_notes(
            page=page,
            page_size=page_size,
            keyword=keyword,
            book_id=book_id,
        )

        items = []
        for raw in notes:
            n = normalize_record(raw)
            mu = n.get("model_usage", {})
            items.append({
                "record_id": n.get("record_id", ""),
                "record_type": n.get("record_type", "chunk_note"),
                "book_id": n.get("book_id", ""),
                "book_title": n.get("book_title", ""),
                "chapter_title": n.get("chapter_title", ""),
                "chunk_index": n.get("chunk_index", 0),
                "chunk_total": n.get("chunk_total", 0),
                "summary": n.get("summary", ""),
                "detail": n.get("detail", ""),
                "reflection": n.get("reflection", ""),
                "memory_note": n.get("memory_note", ""),
                "share_message": n.get("share_message", ""),
                "open_questions": n.get("open_questions", []),
                "tags": n.get("tags", []),
                "importance_score": n.get("importance_score", 0.0),
                "needs_deeper_review": n.get("needs_deeper_review", False),
                "provider_id": mu.get("provider_id", ""),
                "stage": mu.get("stage", ""),
                "strategy": mu.get("strategy", ""),
                "created_at": n.get("created_at", ""),
                "note_id": n.get("record_id", n.get("note_id", "")),
                "chapter": n.get("chapter_title", n.get("chapter", "")),
            })

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def get_note_detail(self, book_id: str, note_id: str) -> dict | None:
        if not self.validate_book_id(book_id):
            raise ValueError("无效的 book_id")
        if not self.validate_note_id(note_id):
            raise ValueError("无效的 note_id")

        note = await self.state_store.get_note_by_id(book_id, note_id)
        if note is None:
            return None

        n = normalize_record(note)
        state = await self.state_store.load_state()
        book = state.get("books", {}).get(book_id, {})
        n["book_title"] = book.get("title", book_id)
        mu = n.get("model_usage", {})

        return {
            "record_id": n.get("record_id", ""),
            "record_type": n.get("record_type", "chunk_note"),
            "book_id": n.get("book_id", ""),
            "book_title": n.get("book_title", ""),
            "chapter_title": n.get("chapter_title", ""),
            "chunk_index": n.get("chunk_index", 0),
            "chunk_total": n.get("chunk_total", 0),
            "summary": n.get("summary", ""),
            "detail": n.get("detail", ""),
            "reflection": n.get("reflection", ""),
            "memory_note": n.get("memory_note", ""),
            "share_message": n.get("share_message", ""),
            "open_questions": n.get("open_questions", []),
            "tags": n.get("tags", []),
            "importance_score": n.get("importance_score", 0.0),
            "needs_deeper_review": n.get("needs_deeper_review", False),
            "deeper_review_done": n.get("deeper_review_done", False),
            "parent_record_ids": n.get("parent_record_ids", []),
            "model_usage": mu,
            "created_at": n.get("created_at", ""),
            "note_id": n.get("record_id", n.get("note_id", "")),
            "chapter": n.get("chapter_title", n.get("chapter", "")),
            "provider_id": mu.get("provider_id", ""),
            "stage": mu.get("stage", ""),
            "strategy": mu.get("strategy", ""),
        }

    async def export_notes(self, book_id: str = "") -> Path | None:
        """导出笔记为 JSONL 文件，返回临时文件路径。"""
        if not self.config_service.get("webui_notes_export_enabled", True):
            raise PermissionError("笔记导出功能已关闭")

        notes_dir = self.data_dir / "notes"
        if not notes_dir.exists():
            return None

        export_path = self.data_dir / "export_notes.jsonl"

        with open(export_path, "w", encoding="utf-8") as out:
            for p in sorted(notes_dir.glob("*.notes.jsonl")):
                bid = p.stem.replace(".notes", "")
                if book_id and bid != book_id:
                    continue
                state = await self.state_store.load_state()
                book_title = state.get("books", {}).get(bid, {}).get("title", bid)
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            note = json.loads(line)
                            note["book_title"] = book_title
                            out.write(json.dumps(note, ensure_ascii=False) + "\n")
                        except json.JSONDecodeError:
                            continue

        return export_path

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def export_books_backup(self) -> Path | None:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.export_books()

    async def export_notes_backup(self) -> Path | None:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.export_notes()

    async def export_full_backup(self) -> Path | None:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.export_full()

    async def parse_backup(self, upload) -> dict:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.parse_backup(upload)

    async def import_backup_merge(self, upload) -> dict:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.import_backup_merge(upload)

    async def get_backup_history(self) -> list[dict]:
        if self.backup_service is None:
            return []
        return await self.backup_service.get_history()

    # ------------------------------------------------------------------
    # 删除（受 webui_delete_enabled 开关控制）
    # ------------------------------------------------------------------

    def _check_delete_enabled(self):
        if not self.config_service.get("webui_delete_enabled", False):
            raise PermissionError("删除功能未开启。请在插件设置 → 页面设置中开启 webui_delete_enabled。")

    async def delete_book(self, book_id: str) -> dict:
        self._check_delete_enabled()
        if not self.validate_book_id(book_id):
            raise ValueError("无效的 book_id")

        # 检查是否有关联笔记
        notes_count = await self.state_store.count_notes_for_book(book_id)
        if notes_count > 0:
            raise ValueError(
                f"该书仍有 {notes_count} 条关联笔记，请先逐条删除笔记后再删除书籍。"
            )

        deleted = await self.state_store.delete_book(book_id)
        if deleted is None:
            raise ValueError("book not found")

        logger.info(
            f"[AutoRead WebUI] Book deleted: {book_id} title={deleted.get('title', '?')}"
        )
        return {
            "deleted": True,
            "book_id": book_id,
            "title": deleted.get("title", ""),
            "message": f"已删除书籍: {deleted.get('title', book_id)}",
        }

    async def delete_note(self, book_id: str, record_id: str) -> dict:
        self._check_delete_enabled()
        if not self.validate_book_id(book_id):
            raise ValueError("无效的 book_id")
        if not self.validate_note_id(record_id):
            raise ValueError("无效的 record_id")

        deleted = await self.state_store.delete_note(book_id, record_id)
        if deleted is None:
            raise ValueError("note not found")

        logger.info(
            f"[AutoRead WebUI] Note deleted: book={book_id} record={record_id}"
        )
        return {
            "deleted": True,
            "book_id": book_id,
            "record_id": record_id,
            "message": "已删除笔记",
        }

    # ------------------------------------------------------------------
    # 任务管理（无需开关）
    # ------------------------------------------------------------------

    async def cancel_task(self, masked_id: str) -> dict:
        """通过 masked session id 取消阅读任务。"""
        umo = await self.state_store.resolve_session_umo(masked_id)
        if umo is None:
            raise ValueError(f"无效的 session_id: {masked_id}")
        session = await self.state_store.get_session(umo)
        if session is None:
            raise ValueError("session not found")
        await self.state_store.stop_session(umo)
        logger.info(f"[AutoRead WebUI] Task cancelled: {masked_id}")
        return {
            "cancelled": True,
            "session_id": masked_id,
            "message": "已取消阅读任务",
        }

    async def clear_finished_tasks(self) -> dict:
        """清理所有已停止/已完成/无书籍的 session 记录。"""
        sessions = await self.state_store.list_sessions()
        cleared = 0
        for umo, s in list(sessions.items()):
            enabled = s.get("enabled", True)
            has_book = bool(s.get("current_book_id"))
            if not enabled or not has_book:
                try:
                    await self.state_store.clear_session(umo)
                    cleared += 1
                except Exception:
                    pass
        logger.info(f"[AutoRead WebUI] Cleared {cleared} finished tasks")
        return {
            "cleared": cleared,
            "message": f"已清理 {cleared} 个历史任务记录",
        }

    # ------------------------------------------------------------------
    # 错误管理
    # ------------------------------------------------------------------

    async def clear_error(self) -> dict:
        count = await self.state_store.clear_all_last_errors()
        logger.info(f"[AutoRead WebUI] Cleared {count} last_error(s)")
        return {
            "cleared": count,
            "message": f"已清除 {count} 条最后错误记录",
        }

    # ------------------------------------------------------------------
    # 状态查询（供前端初始化时读取 capabilities）
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        delete_enabled = self.config_service.get("webui_delete_enabled", False)
        ttl = int(self.config_service.get("webui_last_error_ttl_minutes", 30))
        return {
            "capabilities": {
                "delete_books": delete_enabled,
                "delete_notes": delete_enabled,
                "manage_tasks": True,
                "clear_error": True,
            },
            "config": {
                "webui_delete_enabled": delete_enabled,
                "webui_last_error_ttl_minutes": ttl,
            },
            "capabilities_extended": {
                "backup_list": True,
                "backup_export": True,
                "backup_upload": True,
                "backup_restore": True,
                "backup_delete": delete_enabled,
                "note_delete_by_webui": delete_enabled,
                "note_delete_by_natural_language": False,
                "note_restore_from_backup": True,
                "note_manual_create": False,
                "note_manual_edit": False,
                "reread_by_natural_language": True,
            },
        }

    # ------------------------------------------------------------------
    # 备份管理
    # ------------------------------------------------------------------

    async def list_backups(self) -> list[dict]:
        if self.backup_service is None:
            return []
        return await self.backup_service.list_backups()

    async def delete_backup(self, name: str) -> dict:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        ok = await self.backup_service.delete_backup(name)
        if not ok:
            raise ValueError(f"备份文件不存在: {name}")
        return {"deleted": True, "name": name, "message": f"已删除备份: {name}"}

    async def restore_backup(self, name: str) -> dict:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        result = await self.backup_service.restore_from_backup(name)
        return {"restored": True, "name": name, **result}

    async def upload_backup_file(self, upload) -> dict:
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.save_uploaded_backup(upload)

    async def export_to_server(self, backup_type: str) -> dict:
        """导出备份到服务器并返回元信息。"""
        if self.backup_service is None:
            raise RuntimeError("备份服务未初始化")
        return await self.backup_service.export_to_server(backup_type)

    def get_backup_file_path(self, name: str) -> Path | None:
        """获取备份文件路径（用于下载）。防路径穿越。"""
        if self.backup_service is None:
            return None
        return self.backup_service.get_backup_path(name)
