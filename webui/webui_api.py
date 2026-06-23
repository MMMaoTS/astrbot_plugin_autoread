"""WebUI API 路由处理层。

- handler 按 register_web_api 协议接收 path params。
- 通过 Quart request 代理读取请求数据（handler 运行在 Quart test context 内）。
- 返回 dict（自动转为 JSONResponse）或 FileResponse。
- 不依赖 astrbot.api.web。
"""

from pathlib import Path

from astrbot.api import logger
from starlette.responses import FileResponse

from .webui_service import WebUIService

PLUGIN_NAME = "astrbot_plugin_autoread"


# ---------------------------------------------------------------------------
# 异步上传文件包装
# ---------------------------------------------------------------------------

class _AsyncUploadFile:
    """包装 Quart FileStorage / Starlette UploadFile，统一提供 async read()。

    Quart 的 request.files 返回 FileStorage，其 read() 是同步的；
    但 webui_service 使用 await upload.read()。本包装类统一为 async 接口。
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
    """读取 JSON 请求体。"""
    req = _get_quart_request()
    if req is None:
        return {}
    if hasattr(req, "get_json"):
        data = await req.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return {}


async def _upload_files():
    """读取上传文件 multipart form。"""
    req = _get_quart_request()
    if req is None:
        return {}
    try:
        files = await req.files
        return files
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# WebUI API
# ---------------------------------------------------------------------------

class AutoReadWebUIAPI:
    """AutoRead WebUI API 路由注册器。"""

    def __init__(self, context, webui_service: WebUIService):
        self.context = context
        self.webui = webui_service

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
            logger.info("[AutoRead WebUI] WebUI API routes registered")
        except Exception:
            logger.exception("[AutoRead WebUI] Failed to register web routes")

    # ==================================================================
    # Overview
    # ==================================================================

    async def _overview(self):
        try:
            return await self.webui.get_overview()
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] overview error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    # ==================================================================
    # Books
    # ==================================================================

    async def _list_books(self):
        try:
            q = _query_str("query")
            page = _query_int("page", 1, 1, 10000)
            ps = _query_int("page_size", 20, 1, 100)
            return await self.webui.list_books(query=q, page=page, page_size=ps)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] list_books error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _get_book_detail(self, book_id: str):
        if not WebUIService.validate_book_id(book_id):
            return {"status": "error", "message": "无效的 book_id", "data": None}
        try:
            data = await self.webui.get_book_detail(book_id)
            if data is None:
                return {"status": "error", "message": "book not found", "data": None}
            return data
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] book_detail error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _upload_book(self):
        try:
            files = await _upload_files()
            upload = files.get("file") if files else None
            if upload is None:
                return {
                    "status": "error",
                    "message": "缺少上传文件 (字段名: file)",
                    "data": None,
                }
            wrapped = _AsyncUploadFile(upload)
            result = await self.webui.upload_book_file(wrapped)
            import_result = await self.webui.import_uploaded_book(
                stored_filename=result["stored_filename"]
            )
            result.update(import_result)
            return result
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "data": None}
        except PermissionError as exc:
            return {"status": "error", "message": str(exc), "data": None}
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] upload error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    # ==================================================================
    # Sessions
    # ==================================================================

    async def _list_sessions(self):
        try:
            return await self.webui.list_sessions()
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] sessions error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    # ==================================================================
    # Notes
    # ==================================================================

    async def _get_notes(self):
        try:
            book_id = _query_str("book_id", "", 100)
            page = _query_int("page", 1, 1, 10000)
            ps = _query_int("page_size", 20, 1, 100)
            kw = _query_str("keyword", "", 100)
            return await self.webui.get_notes(
                book_id=book_id, page=page, page_size=ps, keyword=kw
            )
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "data": None}
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] notes error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _get_note_detail(self, book_id: str, note_id: str):
        if not WebUIService.validate_book_id(book_id):
            return {"status": "error", "message": "无效的 book_id", "data": None}
        if not WebUIService.validate_note_id(note_id):
            return {"status": "error", "message": "无效的 note_id", "data": None}
        try:
            data = await self.webui.get_note_detail(book_id, note_id)
            if data is None:
                return {"status": "error", "message": "note not found", "data": None}
            return data
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] note_detail error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    # ==================================================================
    # Settings
    # ==================================================================

    async def _get_settings(self):
        """返回 self.config 真实配置（分组结构）。"""
        try:
            return await self.webui.get_settings()
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] get_settings error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _update_settings(self):
        """更新 self.config 并调用 save_config()。"""
        try:
            body = await _json_body()
            if not body or "settings" not in body:
                return {
                    "status": "error",
                    "message": "请求体缺少 settings 字段",
                    "data": None,
                }
            patch = body["settings"]
            if not isinstance(patch, dict):
                return {
                    "status": "error",
                    "message": "settings 必须是 JSON 对象",
                    "data": None,
                }
            return await self.webui.update_settings(patch)
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "data": None}
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] update_settings error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _list_providers(self):
        try:
            return await self.webui.list_providers()
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] providers error: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    # ==================================================================
    # Backup
    # ==================================================================

    async def _backup_export_books(self):
        try:
            path = await self.webui.export_books_backup()
            if path is None:
                return {"status": "error", "message": "导出失败", "data": None}
            return FileResponse(str(path), filename=Path(path).name)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup export books: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _backup_export_notes(self):
        try:
            path = await self.webui.export_notes_backup()
            if path is None:
                return {"status": "error", "message": "导出失败", "data": None}
            return FileResponse(str(path), filename=Path(path).name)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup export notes: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _backup_export_full(self):
        try:
            path = await self.webui.export_full_backup()
            if path is None:
                return {"status": "error", "message": "导出失败", "data": None}
            return FileResponse(str(path), filename=Path(path).name)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup export full: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _backup_import_preview(self):
        try:
            files = await _upload_files()
            upload = files.get("file") if files else None
            if upload is None:
                return {
                    "status": "error",
                    "message": "缺少上传文件 (字段名: file)",
                    "data": None,
                }
            wrapped = _AsyncUploadFile(upload)
            return await self.webui.parse_backup(wrapped)
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "data": None}
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup preview: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _backup_import_apply(self):
        try:
            files = await _upload_files()
            upload = files.get("file") if files else None
            if upload is None:
                return {
                    "status": "error",
                    "message": "缺少上传文件 (字段名: file)",
                    "data": None,
                }
            wrapped = _AsyncUploadFile(upload)
            return await self.webui.import_backup_merge(wrapped)
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "data": None}
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup import: {exc}")
            return {"status": "error", "message": str(exc), "data": None}

    async def _backup_history(self):
        try:
            items = await self.webui.get_backup_history()
            return {"items": items}
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] backup history: {exc}")
            return {"status": "error", "message": str(exc), "data": None}
