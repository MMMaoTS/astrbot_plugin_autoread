"""WebUI API 路由处理层。

负责：
- 注册 Web API 路由
- 从 request 读取参数、JSON body、上传文件
- 输入校验的第一层处理
- 调用 WebUIService
- 返回 json_response / error_response / file_response

不直接读写 state.json、notes.jsonl、chunks.json。
"""

from astrbot.api import logger
from astrbot.api.web import request, json_response, error_response, file_response, PluginUploadFile

from .webui_service import WebUIService

PLUGIN_NAME = "astrbot_plugin_autoread"


class AutoReadWebUIAPI:
    """AutoRead WebUI API 路由注册器。"""

    def __init__(self, context, webui_service: WebUIService):
        self.context = context
        self.webui = webui_service

    def register_routes(self):
        ctx = self.context
        p = PLUGIN_NAME

        # ---- Overview ----
        ctx.register_web_api(
            f"/{p}/overview",
            self._overview,
            ["GET"],
            "AutoRead overview stats",
        )

        # ---- Books ----
        ctx.register_web_api(
            f"/{p}/books",
            self._list_books,
            ["GET"],
            "List AutoRead books",
        )
        ctx.register_web_api(
            f"/{p}/books/<book_id>",
            self._get_book_detail,
            ["GET"],
            "Get AutoRead book detail",
        )
        ctx.register_web_api(
            f"/{p}/books/upload",
            self._upload_book,
            ["POST"],
            "Upload book file",
        )

        # ---- Sessions ----
        ctx.register_web_api(
            f"/{p}/sessions",
            self._list_sessions,
            ["GET"],
            "List AutoRead sessions (masked)",
        )

        # ---- Notes (read-only) ----
        ctx.register_web_api(
            f"/{p}/notes",
            self._get_notes,
            ["GET"],
            "List AutoRead notes (read-only)",
        )
        ctx.register_web_api(
            f"/{p}/notes/<book_id>/<note_id>",
            self._get_note_detail,
            ["GET"],
            "Get AutoRead note detail (read-only)",
        )

        # ---- Settings ----
        ctx.register_web_api(
            f"/{p}/settings",
            self._get_settings,
            ["GET"],
            "Get AutoRead settings",
        )
        ctx.register_web_api(
            f"/{p}/settings",
            self._update_settings,
            ["POST"],
            "Update AutoRead settings",
        )

        # ---- Providers ----
        ctx.register_web_api(
            f"/{p}/providers",
            self._list_providers,
            ["GET"],
            "List available providers for AutoRead",
        )

        logger.info(f"[AutoRead WebUI] Registered {10} web API routes")

    # ==================================================================
    # 辅助函数
    # ==================================================================

    @staticmethod
    def _get_query_int(key: str, default: int = 1, min_val: int = 1, max_val: int = 100) -> int:
        try:
            val = int(request.args.get(key, str(default)))
            return max(min_val, min(val, max_val))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _get_query_str(key: str, default: str = "", max_len: int = 100) -> str:
        val = (request.args.get(key, "") or "").strip()
        return val[:max_len]

    # ==================================================================
    # Overview
    # ==================================================================

    async def _overview(self):
        try:
            data = await self.webui.get_overview()
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] overview error: {exc}")
            return error_response(str(exc), status_code=500)

    # ==================================================================
    # Books
    # ==================================================================

    async def _list_books(self):
        try:
            query = self._get_query_str("query")
            page = self._get_query_int("page", 1, 1, 10000)
            page_size = self._get_query_int("page_size", 20, 1, 100)
            data = await self.webui.list_books(query=query, page=page, page_size=page_size)
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] list_books error: {exc}")
            return error_response(str(exc), status_code=500)

    async def _get_book_detail(self, book_id: str):
        if not WebUIService.validate_book_id(book_id):
            return error_response("无效的 book_id", status_code=400)
        try:
            data = await self.webui.get_book_detail(book_id)
            if data is None:
                return error_response("book not found", status_code=404)
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] book_detail error: {exc}")
            return error_response(str(exc), status_code=500)

    async def _upload_book(self):
        try:
            files = await request.files()
            upload = files.get("file")
            if upload is None:
                return error_response("缺少上传文件 (字段名: file)", status_code=400)

            result = await self.webui.upload_book_file(upload)

            # 上传后自动导入
            import_result = await self.webui.import_uploaded_book(
                stored_filename=result["stored_filename"],
            )
            result.update(import_result)

            return json_response(result)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except PermissionError as exc:
            return error_response(str(exc), status_code=403)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] upload error: {exc}")
            return error_response(str(exc), status_code=500)

    # ==================================================================
    # Sessions
    # ==================================================================

    async def _list_sessions(self):
        try:
            data = await self.webui.list_sessions()
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] list_sessions error: {exc}")
            return error_response(str(exc), status_code=500)

    # ==================================================================
    # Settings
    # ==================================================================

    async def _get_settings(self):
        try:
            data = await self.webui.get_settings()
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] get_settings error: {exc}")
            return error_response(str(exc), status_code=500)

    async def _update_settings(self):
        try:
            body = await request.get_json()
            if not body or "settings" not in body:
                return error_response("请求体缺少 settings 字段", status_code=400)
            patch = body["settings"]
            if not isinstance(patch, dict):
                return error_response("settings 必须是 JSON 对象", status_code=400)
            data = await self.webui.update_settings(patch)
            return json_response(data)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] update_settings error: {exc}")
            return error_response(str(exc), status_code=500)

    async def _list_providers(self):
        try:
            data = await self.webui.list_providers()
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] list_providers error: {exc}")
            return error_response(str(exc), status_code=500)

    # ==================================================================
    # Notes (read-only)
    # ==================================================================

    async def _get_notes(self):
        try:
            book_id = self._get_query_str("book_id", "", 100)
            page = self._get_query_int("page", 1, 1, 10000)
            page_size = self._get_query_int("page_size", 20, 1, 100)
            keyword = self._get_query_str("keyword", "", 100)
            data = await self.webui.get_notes(
                book_id=book_id, page=page, page_size=page_size, keyword=keyword
            )
            return json_response(data)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] notes error: {exc}")
            return error_response(str(exc), status_code=500)

    async def _get_note_detail(self, book_id: str, note_id: str):
        if not WebUIService.validate_book_id(book_id):
            return error_response("无效的 book_id", status_code=400)
        if not WebUIService.validate_note_id(note_id):
            return error_response("无效的 note_id", status_code=400)
        try:
            data = await self.webui.get_note_detail(book_id, note_id)
            if data is None:
                return error_response("note not found", status_code=404)
            return json_response(data)
        except Exception as exc:
            logger.error(f"[AutoRead WebUI] note_detail error: {exc}")
            return error_response(str(exc), status_code=500)
