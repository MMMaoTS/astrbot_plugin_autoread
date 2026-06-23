"""WebUI API 路由处理层。

- handler 按 register_web_api 协议接收 path params。
- 通过 Quart request 代理读取请求数据（handler 运行在 Quart test context 内）。
- 返回 dict（自动转为 JSONResponse）或 FileResponse。
- 不依赖 astrbot.api.web。
"""

from pathlib import Path

from astrbot.api import logger
from starlette.responses import FileResponse

from .page_service import WebUIService

PLUGIN_NAME = "astrbot_plugin_autoread"


# ---------------------------------------------------------------------------
# 异步上传文件包装
# ---------------------------------------------------------------------------

class _AsyncUploadFile:
    """包装 Quart FileStorage / Starlette UploadFile，统一提供 async read()。

    Quart 的 request.files 返回 FileStorage，其 read() 是同步的；
    但 page_service 使用 await upload.read()。本包装类统一为 async 接口。
    """

    def __init__(self, raw):
        self._raw = raw
        self.filename = getattr(raw, "filename", None) or "unknown"
        self.content_type = getattr(raw, "content_type", None)

    async def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        # 如果 read 返回协程，await 它；否则直接返回 bytes
        if hasattr(data, "__await__"):
            return await data
        return data


# ---------------------------------------------------------------------------
# 请求读取（基于 Quart request，在 bind_quart_request_context 中可用）
# ---------------------------------------------------------------------------

def _get_quart_request():
    """获取当前 Quart request 代理。

    Handler 运行在 call_request_view → bind_quart_request_context 创建的
    Quart test_request_context 内，因此 quart.request 可用。
    """
    try:
        from quart import request as _qr
        return _qr
    except ImportError:
        return None


def _query_str(key: str, default: str = "", max_len: int = 100) -> str:
    """读取查询字符串参数。"""
    req = _get_quart_request()
    if req is None:
        return default
    val = (req.args.get(key, "") or "").strip()
    return val[:max_len] if val else default


def _query_int(key: str, default: int = 1, min_val: int = 1, max_val: int = 100) -> int:
    """读取整数查询参数。"""
    try:
        val = int(_query_str(key, str(default)))
        return max(min_val, min(val, max_val))
    except (ValueError, TypeError):
        return default


async def _json_body():
    """读取 JSON 请求体。

    依赖 quart.request（由 call_request_view → bind_quart_request_context 提供）。
    如果 quart.request 上下文不可用，返回空 dict；handler 层会检测并返回明确错误。
    """
    req = _get_quart_request()
    if req is None:
        logger.warning("[AutoRead WebUI] quart.request 不可用（quart 未安装），无法读取请求体")
        return {}
    try:
        if hasattr(req, "get_json"):
            data = await req.get_json(silent=True)
            return data if isinstance(data, dict) else {}
    except RuntimeError as exc:
        logger.warning(
            f"[AutoRead WebUI] quart.request 上下文不可用（{exc}），"
            "请确认 Dashboard 已启用 app_adapter 模式"
        )
    except Exception as exc:
        logger.error(f"[AutoRead WebUI] 读取请求体失败: {exc}")
    return {}


async def _upload_files():
    """读取上传文件 multipart form。

    依赖 quart.request（由 call_request_view → bind_quart_request_context 提供）。
    """
    req = _get_quart_request()
    if req is None:
        return {}
    try:
        files = await req.files
        return files
    except RuntimeError as exc:
        logger.warning(
            f"[AutoRead WebUI] quart.request 上下文不可用（{exc}），"
            "无法读取上传文件"
        )
    except Exception as exc:
        logger.error(f"[AutoRead WebUI] 读取上传文件失败: {exc}")
    return {}


# ---------------------------------------------------------------------------
# WebUI API
# ---------------------------------------------------------------------------

class AutoReadWebUIAPI:
    """AutoRead WebUI API 路由注册器。"""

    def __init__(self, context, webui_service: WebUIService):
        self.context = context
        self.webui = webui_service

    # ------------------------------------------------------------------
    # 响应格式
    # ------------------------------------------------------------------

    @staticmethod
    def _ok(data):
        """成功响应：status="ok" 时 Dashboard 自动解包 data 字段返回给前端。"""
        return {"status": "ok", "success": True, "data": data}

    @staticmethod
    def _err(message: str):
        """错误响应：status="error" 时 Dashboard 抛出异常，前端 catch 显示 message。"""
        return {"status": "error", "message": message}

    def register_routes(self):
        if not hasattr(self.context, "register_web_api"):
            logger.warning(
                "[AutoRead WebUI] register_web_api not available, WebUI API disabled"
            )
            return

        ctx = self.context
        p = PLUGIN_NAME
        try:
            ctx.register_web_api(
                f"/{p}/overview", self._overview, ["GET"], "AutoRead overview"
            )
            ctx.register_web_api(
                f"/{p}/books", self._list_books, ["GET"], "List books"
            )
            ctx.register_web_api(
                f"/{p}/books/<book_id>",
                self._get_book_detail,
                ["GET"],
                "Book detail",
            )
            ctx.register_web_api(
                f"/{p}/books/upload", self._upload_book, ["POST"], "Upload book"
            )
            ctx.register_web_api(
                f"/{p}/sessions", self._list_sessions, ["GET"], "List sessions"
            )
            ctx.register_web_api(
                f"/{p}/notes", self._get_notes, ["GET"], "List notes"
            )
            ctx.register_web_api(
                f"/{p}/notes/<book_id>/<note_id>",
                self._get_note_detail,
                ["GET"],
                "Note detail",
            )
            ctx.register_web_api(
                f"/{p}/settings", self._get_settings, ["GET"], "Get settings"
            )
            ctx.register_web_api(
                f"/{p}/settings",
                self._update_settings,
                ["POST"],
                "Update settings",
            )
            ctx.register_web_api(
                f"/{p}/providers",
                self._list_providers,
                ["GET"],
                "List providers",
            )
            ctx.register_web_api(
                f"/{p}/backup/export/books",
                self._backup_export_books,
                ["GET"],
                "Export books",
            )
            ctx.register_web_api(
                f"/{p}/backup/export/notes",
                self._backup_export_notes,
                ["GET"],
                "Export notes",
            )
            ctx.register_web_api(
                f"/{p}/backup/export/full",
                self._backup_export_full,
                ["GET"],
                "Export full",
            )
            ctx.register_web_api(
                f"/{p}/backup/import/preview",
                self._backup_import_preview,
                ["POST"],
                "Import preview",
            )
            ctx.register_web_api(
                f"/{p}/backup/import/apply",
                self._backup_import_apply,
                ["POST"],
                "Import apply",
            )
            ctx.register_web_api(
                f"/{p}/backup/history",
                self._backup_history,
                ["GET"],
                "Import history",
            )
            ctx.register_web_api(
                f"/{p}/books/delete",
                self._delete_book,
                ["POST"],
                "Delete book (requires webui_delete_enabled)",
            )
            ctx.register_web_api(
                f"/{p}/notes/delete",
                self._delete_note,
                ["POST"],
                "Delete note (requires webui_delete_enabled)",
            )
            ctx.register_web_api(
                f"/{p}/sessions/cancel",
                self._cancel_task,
                ["POST"],
                "Cancel reading task by session_id",
            )
            ctx.register_web_api(
                f"/{p}/sessions/clear-finished",
                self._clear_finished_tasks,
                ["POST"],
                "Clear finished/cancelled task records",
            )
            ctx.register_web_api(
                f"/{p}/status/clear-error",
                self._clear_error,
                ["POST"],
                "Clear all last_error records",
            )
            ctx.register_web_api(
                f"/{p}/status",
                self._get_status,
                ["GET"],
                "Get capabilities and config status",
            )
            logger.info("[AutoRead WebUI] WebUI API routes registered")
        except Exception:
            logger.exception("[AutoRead WebUI] Failed to register web routes")

    # ==================================================================
    # Overview
    # ==================================================================

    async def _overview(self):
        try:
            return self._ok(await self.webui.get_overview())
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] overview error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # Books
    # ==================================================================

    async def _list_books(self):
        try:
            q = _query_str("query")
            page = _query_int("page", 1, 1, 10000)
            ps = _query_int("page_size", 20, 1, 100)
            return self._ok(await self.webui.list_books(query=q, page=page, page_size=ps))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] list_books error: {exc}")
            return self._err(str(exc))

    async def _get_book_detail(self, book_id: str):
        if not WebUIService.validate_book_id(book_id):
            return self._err("无效的 book_id")
        try:
            data = await self.webui.get_book_detail(book_id)
            if data is None:
                return self._err("book not found")
            return self._ok(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] book_detail error: {exc}")
            return self._err(str(exc))

    async def _upload_book(self):
        try:
            files = await _upload_files()
            upload = files.get("file") if files else None
            if upload is None:
                return self._err("缺少上传文件 (字段名: file)")
            wrapped = _AsyncUploadFile(upload)
            result = await self.webui.upload_book_file(wrapped)
            import_result = await self.webui.import_uploaded_book(
                stored_filename=result["stored_filename"]
            )
            result.update(import_result)
            return self._ok(result)
        except ValueError as exc:
            return self._err(str(exc))
        except PermissionError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] upload error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # Sessions
    # ==================================================================

    async def _list_sessions(self):
        try:
            return self._ok(await self.webui.list_sessions())
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] sessions error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # Notes
    # ==================================================================

    async def _get_notes(self):
        try:
            book_id = _query_str("book_id", "", 100)
            page = _query_int("page", 1, 1, 10000)
            ps = _query_int("page_size", 20, 1, 100)
            kw = _query_str("keyword", "", 100)
            return self._ok(await self.webui.get_notes(
                book_id=book_id, page=page, page_size=ps, keyword=kw
            ))
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] notes error: {exc}")
            return self._err(str(exc))

    async def _get_note_detail(self, book_id: str, note_id: str):
        if not WebUIService.validate_book_id(book_id):
            return self._err("无效的 book_id")
        if not WebUIService.validate_note_id(note_id):
            return self._err("无效的 note_id")
        try:
            data = await self.webui.get_note_detail(book_id, note_id)
            if data is None:
                return self._err("note not found")
            return self._ok(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] note_detail error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # Settings
    # ==================================================================

    async def _get_settings(self):
        """返回 self.config 真实配置（分组结构）。"""
        try:
            return self._ok(await self.webui.get_settings())
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] get_settings error: {exc}")
            return self._err(str(exc))

    async def _update_settings(self):
        """更新 self.config 并调用 save_config()。"""
        try:
            body = await _json_body()
            if not body or "settings" not in body:
                return self._err("请求体缺少 settings 字段")
            patch = body["settings"]
            if not isinstance(patch, dict):
                return self._err("settings 必须是 JSON 对象")
            return self._ok(await self.webui.update_settings(patch))
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] update_settings error: {exc}")
            return self._err(str(exc))

    async def _list_providers(self):
        try:
            return self._ok(await self.webui.list_providers())
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] providers error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # Backup
    # ==================================================================

    async def _backup_export_books(self):
        try:
            path = await self.webui.export_books_backup()
            if path is None:
                return self._err("导出失败")
            return FileResponse(str(path), filename=Path(path).name)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup export books: {exc}")
            return self._err(str(exc))

    async def _backup_export_notes(self):
        try:
            path = await self.webui.export_notes_backup()
            if path is None:
                return self._err("导出失败")
            return FileResponse(str(path), filename=Path(path).name)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup export notes: {exc}")
            return self._err(str(exc))

    async def _backup_export_full(self):
        try:
            path = await self.webui.export_full_backup()
            if path is None:
                return self._err("导出失败")
            return FileResponse(str(path), filename=Path(path).name)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup export full: {exc}")
            return self._err(str(exc))

    async def _backup_import_preview(self):
        try:
            files = await _upload_files()
            upload = files.get("file") if files else None
            if upload is None:
                return self._err("缺少上传文件 (字段名: file)")
            wrapped = _AsyncUploadFile(upload)
            return self._ok(await self.webui.parse_backup(wrapped))
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup preview: {exc}")
            return self._err(str(exc))

    async def _backup_import_apply(self):
        try:
            files = await _upload_files()
            upload = files.get("file") if files else None
            if upload is None:
                return self._err("缺少上传文件 (字段名: file)")
            wrapped = _AsyncUploadFile(upload)
            return self._ok(await self.webui.import_backup_merge(wrapped))
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup import: {exc}")
            return self._err(str(exc))

    async def _backup_history(self):
        try:
            items = await self.webui.get_backup_history()
            return self._ok({"items": items})
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup history: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # 删除
    # ==================================================================

    async def _delete_book(self):
        try:
            body = await _json_body()
            book_id = str(body.get("book_id", "")).strip()
            logger.info(f"[AutoRead WebUI] delete_book requested: book_id={book_id}")
            if not book_id:
                return self._err("缺少 book_id")
            result = await self.webui.delete_book(book_id)
            logger.info(f"[AutoRead WebUI] delete_book done: {result.get('message', '')}")
            return self._ok(result)
        except ValueError as exc:
            return self._err(str(exc))
        except PermissionError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] delete_book error: {exc}")
            return self._err(str(exc))

    async def _delete_note(self):
        try:
            body = await _json_body()
            book_id = str(body.get("book_id", "")).strip()
            record_id = str(body.get("record_id", "")).strip()
            logger.info(f"[AutoRead WebUI] delete_note requested: book_id={book_id} record_id={record_id}")
            if not book_id:
                return self._err("缺少 book_id")
            if not record_id:
                return self._err("缺少 record_id")
            result = await self.webui.delete_note(book_id, record_id)
            logger.info(f"[AutoRead WebUI] delete_note done: {result.get('message', '')}")
            return self._ok(result)
        except ValueError as exc:
            return self._err(str(exc))
        except PermissionError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] delete_note error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # 任务管理
    # ==================================================================

    async def _cancel_task(self):
        try:
            body = await _json_body()
            session_id = str(body.get("session_id", "")).strip()
            logger.info(f"[AutoRead WebUI] cancel_task requested: session_id={session_id}")
            if not session_id:
                return self._err("缺少 session_id")
            result = await self.webui.cancel_task(session_id)
            logger.info(f"[AutoRead WebUI] cancel_task done: {result.get('message', '')}")
            return self._ok(result)
        except ValueError as exc:
            return self._err(str(exc))
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] cancel_task error: {exc}")
            return self._err(str(exc))

    async def _clear_finished_tasks(self):
        try:
            return self._ok(await self.webui.clear_finished_tasks())
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] clear_finished_tasks error: {exc}")
            return self._err(str(exc))

    # ==================================================================
    # 错误管理
    # ==================================================================

    async def _clear_error(self):
        try:
            logger.info("[AutoRead WebUI] clear_error requested")
            result = await self.webui.clear_error()
            logger.info(f"[AutoRead WebUI] clear_error done: {result}")
            return self._ok(result)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] clear_error error: {exc}")
            return self._err(str(exc))

    async def _get_status(self):
        try:
            return self._ok(await self.webui.get_status())
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] get_status error: {exc}")
            return self._err(str(exc))
