"""备份恢复服务。

负责书籍/笔记的导出、导入预览、合并导入和导入历史。
所有导入均为合并模式：新 ID 导入，已有 ID 跳过，禁止覆盖。
"""

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from astrbot.api import logger


def _now_iso() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()


def _new_backup_id() -> str:
    ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"backup_{ts}_{short}"


def _ts_tag() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")


class BackupService:
    """备份恢复核心。只做合并导入，禁止覆盖。"""

    def __init__(self, data_dir: Path, state_store):
        self.data_dir = data_dir
        self.state_store = state_store
        self.backups_dir = data_dir / "backups"
        self.history_path = self.backups_dir / "import_history.jsonl"
        self._previewed_backup_ids: set[str] = set()
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    # ==================================================================
    # 导出
    # ==================================================================

    async def export_to_server(self, backup_type: str) -> dict:
        """导出备份到服务器并返回元信息。"""
        if backup_type == "books":
            path = await self.export_books()
        elif backup_type == "notes":
            path = await self.export_notes()
        elif backup_type == "full":
            path = await self.export_full()
        else:
            raise ValueError(f"未知备份类型: {backup_type}")

        stat = path.stat()
        # 统计概览
        state = await self.state_store.load_state()
        books_count = len(state.get("books", {}))
        sessions = state.get("sessions", {})
        active_tasks = sum(1 for s in sessions.values() if s.get("current_book_id"))
        notes_count = await self._count_all_notes()

        return {
            "name": path.name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone(timedelta(hours=8))).isoformat(),
            "source": "exported",
            "schema_version": 1,
            "summary": {
                "books_count": books_count,
                "notes_count": notes_count,
                "tasks_count": active_tasks,
            },
        }

    async def _count_all_notes(self) -> int:
        notes_dir = self.data_dir / "notes"
        if not notes_dir.exists():
            return 0
        count = 0
        for p in notes_dir.glob("*.jsonl"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    count += sum(1 for line in f if line.strip())
            except OSError:
                continue
        return count

    async def export_books(self) -> Path:
        """导出书籍备份 zip。返回文件路径。"""
        backup_id = _new_backup_id()
        tmp = tempfile.mkdtemp(prefix="autoread_backup_")
        try:
            root = Path(tmp)
            # manifest
            manifest = {
                "backup_id": backup_id,
                "backup_format_version": 1,
                "plugin_name": "astrbot_plugin_autoread",
                "backup_type": "books",
                "created_at": _now_iso(),
                "schema_version": 1,
                "contains": {"books": True, "notes": False, "state": False},
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # books/
            books_src = self.data_dir / "books"
            if books_src.exists():
                shutil.copytree(books_src, root / "books", dirs_exist_ok=True)

            # chunks/
            chunks_src = self.data_dir / "chunks"
            if chunks_src.exists():
                shutil.copytree(chunks_src, root / "chunks", dirs_exist_ok=True)

            # book_index.json
            state = await self.state_store.load_state()
            books_meta = state.get("books", {})
            (root / "book_index.json").write_text(
                json.dumps(list(books_meta.values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            short = uuid.uuid4().hex[:6]
            zip_path = self.backups_dir / f"autoread_backup_books_{_ts_tag()}_{short}.zip"
            self._make_zip(root, zip_path)
            logger.info(f"[AutoRead Backup] Exported books: {zip_path.name} ({backup_id})")
            return zip_path
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def export_notes(self) -> Path:
        """导出笔记备份 zip。"""
        backup_id = _new_backup_id()
        tmp = tempfile.mkdtemp(prefix="autoread_backup_")
        try:
            root = Path(tmp)
            manifest = {
                "backup_id": backup_id,
                "backup_format_version": 1,
                "plugin_name": "astrbot_plugin_autoread",
                "backup_type": "notes",
                "created_at": _now_iso(),
                "schema_version": 1,
                "contains": {"books": False, "notes": True, "state": False},
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            notes_src = self.data_dir / "notes"
            if notes_src.exists():
                shutil.copytree(notes_src, root / "notes", dirs_exist_ok=True)

            # records_index: collect all record_ids
            index = []
            if notes_src.exists():
                for p in sorted(notes_src.glob("*.jsonl")):
                    book_id = p.stem.replace(".notes", "").replace(".records", "")
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                rec = json.loads(line)
                                index.append({
                                    "record_id": rec.get("record_id", rec.get("note_id", "")),
                                    "book_id": book_id,
                                    "record_type": rec.get("record_type", "chunk_note"),
                                    "created_at": rec.get("created_at", ""),
                                })
                            except json.JSONDecodeError:
                                continue
            (root / "records_index.json").write_text(
                json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            short = uuid.uuid4().hex[:6]
            zip_path = self.backups_dir / f"autoread_backup_notes_{_ts_tag()}_{short}.zip"
            self._make_zip(root, zip_path)
            logger.info(f"[AutoRead Backup] Exported notes: {zip_path.name} ({backup_id})")
            return zip_path
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def export_full(self) -> Path:
        """导出完整备份 zip。"""
        backup_id = _new_backup_id()
        tmp = tempfile.mkdtemp(prefix="autoread_backup_")
        try:
            root = Path(tmp)
            manifest = {
                "backup_id": backup_id,
                "backup_format_version": 1,
                "plugin_name": "astrbot_plugin_autoread",
                "backup_type": "full",
                "created_at": _now_iso(),
                "schema_version": 1,
                "contains": {"books": True, "notes": True, "state": True},
                "import_policy": {"config": "read_only_snapshot_not_imported"},
            }
            (root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            for sub in ("books", "chunks", "notes"):
                src = self.data_dir / sub
                if src.exists():
                    shutil.copytree(src, root / sub, dirs_exist_ok=True)

            # state snapshot
            state = await self.state_store.load_state()
            (root / "state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # state/config snapshots are read-only references and are never restored.
            (root / "book_index.json").write_text(
                json.dumps(list(state.get("books", {}).values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            short = uuid.uuid4().hex[:6]
            zip_path = self.backups_dir / f"autoread_backup_full_{_ts_tag()}_{short}.zip"
            self._make_zip(root, zip_path)
            logger.info(f"[AutoRead Backup] Exported full: {zip_path.name} ({backup_id})")
            return zip_path
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ==================================================================
    # 导入：解析 & 预览
    # ==================================================================

    async def parse_backup(self, upload) -> dict:
        """解析上传的备份 zip 并预览。不写入任何业务数据。"""
        tmp = tempfile.mkdtemp(prefix="autoread_import_")
        try:
            zip_path = Path(tmp) / "backup.zip"
            content = await upload.read()
            zip_path.write_bytes(content)

            if not zipfile.is_zipfile(zip_path):
                raise ValueError("不是有效的 zip 备份包")

            with zipfile.ZipFile(zip_path, "r") as zf:
                # 读取 manifest
                if "manifest.json" not in zf.namelist():
                    raise ValueError("备份包缺少 manifest.json")
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

            backup_id = manifest.get("backup_id", "unknown")
            backup_type = manifest.get("backup_type", "unknown")

            # 检查是否已导入过
            already_imported = self._is_backup_imported(backup_id)

            # 预览计算
            with zipfile.ZipFile(zip_path, "r") as zf:
                if backup_type in ("books", "full"):
                    preview = self._preview_books_import(zf)
                elif backup_type == "notes":
                    preview = self._preview_notes_import(zf)
                else:
                    raise ValueError(f"未知备份类型: {backup_type}")
                if backup_type == "full":
                    books_preview = preview
                    notes_preview = self._preview_notes_import(zf)
                    preview = {
                        "total_items": (
                            books_preview["total_items"] + notes_preview["total_items"]
                        ),
                        "new_items": (
                            books_preview["new_items"] + notes_preview["new_items"]
                        ),
                        "skipped_existing_ids": (
                            books_preview["skipped_existing_ids"]
                            + notes_preview["skipped_existing_ids"]
                        ),
                        "books_total_items": books_preview["total_items"],
                        "books_new_items": books_preview["new_items"],
                        "books_skipped_existing_ids": books_preview[
                            "skipped_existing_ids"
                        ],
                        "notes_total_items": notes_preview["total_items"],
                        "notes_new_items": notes_preview["new_items"],
                        "notes_skipped_existing_ids": notes_preview[
                            "skipped_existing_ids"
                        ],
                    }

            if already_imported:
                preview["already_imported_backup"] = True
                preview["new_items"] = 0
                preview["skipped_existing_ids"] = preview["total_items"]
                if backup_type == "full":
                    preview["books_new_items"] = 0
                    preview["books_skipped_existing_ids"] = preview.get(
                        "books_total_items", 0
                    )
                    preview["notes_new_items"] = 0
                    preview["notes_skipped_existing_ids"] = preview.get(
                        "notes_total_items", 0
                    )
                preview["message"] = "该备份包已导入过，默认跳过。"
            else:
                preview["already_imported_backup"] = False
                preview["message"] = (
                    "可合并导入。已存在 ID 会自动跳过。"
                    if preview["new_items"] > 0
                    else "所有记录均已存在，无需导入。"
                )

            preview["backup_id"] = backup_id
            preview["backup_type"] = backup_type
            self._previewed_backup_ids.add(backup_id)

            return preview
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _preview_books_import(self, zf: zipfile.ZipFile) -> dict:
        existing_books = set()
        state = self.data_dir / "state.json"
        if state.exists():
            try:
                s = json.loads(state.read_text(encoding="utf-8"))
                existing_books = set(s.get("books", {}).keys())
            except Exception:
                pass

        books_list = []
        if "book_index.json" in zf.namelist():
            try:
                raw_books = json.loads(zf.read("book_index.json").decode("utf-8"))
                if isinstance(raw_books, list):
                    books_list = raw_books
            except (json.JSONDecodeError, UnicodeDecodeError):
                books_list = []

        total = 0
        new_count = 0
        skip_count = 0
        if books_list:
            for book in books_list:
                if not isinstance(book, dict):
                    continue
                book_id = book.get("book_id", "")
                if not book_id:
                    continue
                total += 1
                if book_id in existing_books:
                    skip_count += 1
                else:
                    new_count += 1
        else:
            for name in zf.namelist():
                if name.startswith("books/") and not name.endswith("/"):
                    book_id = Path(name).stem
                    total += 1
                    if book_id in existing_books:
                        skip_count += 1
                    else:
                        new_count += 1

        return {"total_items": total, "new_items": new_count, "skipped_existing_ids": skip_count}

    def _preview_notes_import(self, zf: zipfile.ZipFile) -> dict:
        existing_records = self._collect_existing_record_ids()
        total = 0
        new_count = 0
        skip_count = 0
        for name in zf.namelist():
            if name.startswith("notes/") and name.endswith(".jsonl"):
                try:
                    content = zf.read(name).decode("utf-8")
                    for line in content.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            rid = rec.get("record_id", rec.get("note_id", ""))
                            if not rid:
                                continue
                            total += 1
                            if rid in existing_records:
                                skip_count += 1
                            else:
                                new_count += 1
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    continue
        return {"total_items": total, "new_items": new_count, "skipped_existing_ids": skip_count}

    # ==================================================================
    # 导入：执行合并
    # ==================================================================

    async def import_backup_merge(self, upload) -> dict:
        """执行合并导入。新 ID 导入，已有 ID 跳过。"""
        tmp = tempfile.mkdtemp(prefix="autoread_import_")
        try:
            zip_path = Path(tmp) / "backup.zip"
            content = await upload.read()
            zip_path.write_bytes(content)

            if not zipfile.is_zipfile(zip_path):
                raise ValueError("不是有效的 zip 备份包")

            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    raise ValueError("备份包缺少 manifest.json")
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

            backup_id = manifest["backup_id"]
            backup_type = manifest["backup_type"]

            if backup_type not in ("books", "notes", "full"):
                raise ValueError(f"未知备份类型: {backup_type}")

            if backup_id not in self._previewed_backup_ids:
                raise ValueError("请先解析备份并确认预览结果，再执行合并导入。")

            if self._is_backup_imported(backup_id):
                return {
                    "backup_id": backup_id,
                    "status": "skipped",
                    "imported_items": 0,
                    "skipped_existing_ids": 0,
                    "message": "该备份包已导入过。",
                }

            result = {"backup_id": backup_id, "backup_type": backup_type}
            with zipfile.ZipFile(zip_path, "r") as zf:
                imported_total = 0
                skipped_total = 0
                if backup_type in ("books", "full"):
                    books_result = self._do_merge_books(zf)
                    result.update({
                        "imported_books": books_result["imported_items"],
                        "skipped_books": books_result["skipped_existing_ids"],
                    })
                    imported_total += books_result["imported_items"]
                    skipped_total += books_result["skipped_existing_ids"]
                if backup_type in ("notes", "full"):
                    notes_result = self._do_merge_notes(zf)
                    result.update({
                        "imported_notes": notes_result["imported_items"],
                        "skipped_notes": notes_result["skipped_existing_ids"],
                    })
                    imported_total += notes_result["imported_items"]
                    skipped_total += notes_result["skipped_existing_ids"]
                result["imported_items"] = imported_total
                result["skipped_existing_ids"] = skipped_total

            result["status"] = "success"
            self._record_history(backup_id, backup_type, result)
            logger.info(
                f"[AutoRead Backup] Imported: {backup_id} ({backup_type}) "
                f"+{result.get('imported_items', 0)}"
            )
            return result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _do_merge_books(self, zf: zipfile.ZipFile) -> dict:
        state = self.data_dir / "state.json"
        current_books = {}
        if state.exists():
            try:
                current_books = json.loads(state.read_text(encoding="utf-8")).get("books", {})
            except Exception:
                pass

        imported = 0
        skipped = 0

        if "book_index.json" in zf.namelist():
            try:
                books_list = json.loads(zf.read("book_index.json").decode("utf-8"))
                full_state = {}
                if state.exists():
                    full_state = json.loads(state.read_text(encoding="utf-8"))
                full_state.setdefault("version", 1)
                full_state.setdefault("sessions", {})
                full_state.setdefault("books", {})
                for b in books_list:
                    if not isinstance(b, dict):
                        continue
                    bid = b.get("book_id", "")
                    source_path = b.get("source_path", "")
                    chunks_path = b.get("chunks_path", "")
                    if not bid:
                        continue
                    if bid in full_state["books"] or bid in current_books:
                        skipped += 1
                        continue
                    if (
                        not source_path.startswith("books/")
                        or ".." in Path(source_path).parts
                        or source_path not in zf.namelist()
                    ):
                        skipped += 1
                        continue
                    book_dest = self.data_dir / source_path
                    if book_dest.exists():
                        skipped += 1
                        continue
                    chunk_dest = None
                    if chunks_path:
                        if (
                            not chunks_path.startswith("chunks/")
                            or ".." in Path(chunks_path).parts
                            or chunks_path not in zf.namelist()
                        ):
                            skipped += 1
                            continue
                        chunk_dest = self.data_dir / chunks_path
                        if chunk_dest.exists():
                            skipped += 1
                            continue
                    book_dest.parent.mkdir(parents=True, exist_ok=True)
                    book_dest.write_bytes(zf.read(source_path))
                    if chunk_dest is not None:
                        chunk_dest.parent.mkdir(parents=True, exist_ok=True)
                        chunk_dest.write_bytes(zf.read(chunks_path))
                    full_state["books"][bid] = b
                    imported += 1
                tmp_path = state.with_suffix(".json.tmp")
                tmp_path.write_text(
                    json.dumps(full_state, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(tmp_path, state)
            except Exception:
                logger.exception("[AutoRead Backup] Failed to merge books from book_index")
        else:
            for name in zf.namelist():
                if not name.startswith("books/") or name.endswith("/"):
                    continue
                if ".." in Path(name).parts:
                    skipped += 1
                    continue
                book_id = Path(name).stem
                dest = self.data_dir / name
                if dest.exists() or book_id in current_books:
                    skipped += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(name))
                imported += 1

        return {"imported_items": imported, "skipped_existing_ids": skipped}

    def _do_merge_notes(self, zf: zipfile.ZipFile) -> dict:
        existing = self._collect_existing_record_ids()
        imported = 0
        skipped = 0

        for name in zf.namelist():
            if not name.startswith("notes/") or not name.endswith(".jsonl"):
                continue
            if ".." in Path(name).parts:
                continue
            dest = self.data_dir / name
            content = zf.read(name).decode("utf-8")
            new_lines = []
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rid = rec.get("record_id", rec.get("note_id", ""))
                    if not rid:
                        skipped += 1
                        continue
                    if rid in existing:
                        skipped += 1
                    else:
                        new_lines.append(line)
                        existing.add(rid)
                        imported += 1
                except json.JSONDecodeError:
                    continue

            if new_lines:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "a", encoding="utf-8") as f:
                    for ln in new_lines:
                        f.write(ln + "\n")

        return {"imported_items": imported, "skipped_existing_ids": skipped}

    # ==================================================================
    # 导入历史
    # ==================================================================

    def _is_backup_imported(self, backup_id: str) -> bool:
        if not self.history_path.exists():
            return False
        with open(self.history_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if json.loads(line).get("backup_id") == backup_id:
                        return True
                except json.JSONDecodeError:
                    continue
        return False

    def _record_history(self, backup_id: str, backup_type: str, result: dict):
        record = {
            "backup_id": backup_id,
            "backup_type": backup_type,
            "imported_at": _now_iso(),
            "mode": "merge",
            "total_items": result.get("imported_items", 0) + result.get("skipped_existing_ids", 0),
            "imported_items": result.get("imported_items", 0),
            "skipped_existing_ids": result.get("skipped_existing_ids", 0),
            "status": result.get("status", "success"),
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ==================================================================
    # 备份文件管理
    # ==================================================================

    async def list_backups(self) -> list[dict]:
        """列出 backups 目录下的所有备份文件。"""
        if not self.backups_dir.exists():
            return []
        items = []
        for p in sorted(self.backups_dir.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file():
                continue
            stat = p.stat()
            source = "exported" if p.name.startswith("autoread_") else "uploaded"
            items.append({
                "name": p.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone(timedelta(hours=8))).isoformat(),
                "source": source,
            })
        return items

    def get_backup_path(self, name: str) -> Path | None:
        """获取备份文件路径。防路径穿越校验。"""
        safe_name = Path(name).name
        if safe_name != name or ".." in name or name.startswith("/"):
            return None
        target = self.backups_dir / safe_name
        if not target.exists() or not target.is_file():
            return None
        return target

    async def delete_backup(self, name: str) -> bool:
        """删除备份文件。防路径穿越。"""
        target = self.get_backup_path(name)
        if target is None:
            return False
        target.unlink()
        logger.info(f"[AutoRead Backup] Deleted backup file: {target.name}")
        return True

    async def inspect_backup(self, name: str) -> dict:
        """解析/预检服务器上的备份文件，返回元信息。"""
        safe_name = Path(name).name
        if safe_name != name or ".." in name:
            raise ValueError("无效的文件名")
        target = self.backups_dir / safe_name
        if not target.exists():
            raise ValueError(f"备份文件不存在: {safe_name}")
        if not zipfile.is_zipfile(target):
            raise ValueError("不是有效的 zip 备份包")

        with zipfile.ZipFile(target, "r") as zf:
            if "manifest.json" not in zf.namelist():
                raise ValueError("备份包缺少 manifest.json")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            backup_type = manifest.get("backup_type", "")
            if backup_type not in ("books", "notes", "full"):
                raise ValueError(f"未知备份类型: {backup_type}")

            # 统计概览
            books_count = 0
            notes_count = 0
            for entry in zf.namelist():
                if entry.startswith("books/") and not entry.endswith("/"):
                    books_count += 1
                elif entry.startswith("notes/") and entry.endswith(".jsonl"):
                    try:
                        content = zf.read(entry).decode("utf-8")
                        notes_count += sum(1 for line in content.splitlines() if line.strip())
                    except Exception:
                        continue

            already = self._is_backup_imported(manifest.get("backup_id", ""))

        stat = target.stat()
        return {
            "name": safe_name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone(timedelta(hours=8))).isoformat(),
            "backup_id": manifest.get("backup_id", "unknown"),
            "backup_type": backup_type,
            "schema_version": manifest.get("schema_version", 1),
            "created_at": manifest.get("created_at", ""),
            "already_imported": already,
            "summary": {"books_count": books_count, "notes_count": notes_count},
            "restore_mode": "merge",
            "warnings": ["恢复将以合并模式导入，已有 ID 自动跳过。不会覆盖现有数据。"] if not already else ["该备份已导入过，再次恢复将跳过所有记录。"],
        }

    async def restore_from_backup(self, name: str) -> dict:
        """从 backups 目录中的备份文件恢复。自动解析，无需手动 preview。"""
        safe_name = Path(name).name
        if safe_name != name or ".." in name:
            raise ValueError("无效的文件名")
        target = self.backups_dir / safe_name
        if not target.exists():
            raise ValueError(f"备份文件不存在: {safe_name}")
        if not zipfile.is_zipfile(target):
            raise ValueError("不是有效的 zip 备份包")

        with zipfile.ZipFile(target, "r") as zf:
            if "manifest.json" not in zf.namelist():
                raise ValueError("备份包缺少 manifest.json")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))

        backup_id = manifest.get("backup_id", "unknown")
        self._previewed_backup_ids.add(backup_id)

        class _FileUpload:
            def __init__(self, path):
                self.filename = path.name
                self.content_type = "application/zip"
            async def read(self, size=-1):
                return target.read_bytes()

        upload = _FileUpload(target)
        result = await self.import_backup_merge(upload)
        logger.info(f"[AutoRead Backup] Restored from server backup: {safe_name}")
        return result

    async def save_uploaded_backup(self, upload) -> dict:
        """保存上传的备份文件到 backups 目录。不执行恢复。"""
        safe_original = Path(upload.filename).name
        if not safe_original or safe_original.startswith(".") or ".." in safe_original:
            raise ValueError("无效的文件名")
        suffix = Path(safe_original).suffix.lower()
        if suffix not in (".zip",):
            raise ValueError("仅支持 .zip 格式的备份文件")

        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        stored_name = f"autoread_uploaded_{ts}_{safe_original}"
        dest = self.backups_dir / stored_name

        content = await upload.read()
        max_mb = 50  # 可通过配置调整
        if len(content) > max_mb * 1024 * 1024:
            raise ValueError(f"备份文件超过大小限制 ({max_mb} MB)")

        dest.write_bytes(content)

        if not zipfile.is_zipfile(dest):
            dest.unlink()
            raise ValueError("不是有效的 zip 备份包")

        logger.info(f"[AutoRead Backup] Uploaded backup saved: {stored_name} ({len(content)} bytes)")
        return {"name": stored_name, "size": len(content), "source": "uploaded", "message": "备份文件已上传"}

    async def get_history(self) -> list[dict]:
        if not self.history_path.exists():
            return []
        records = []
        with open(self.history_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        records.reverse()
        return records

    # ==================================================================
    # 内部工具
    # ==================================================================

    def _collect_existing_record_ids(self) -> set:
        ids = set()
        notes_dir = self.data_dir / "notes"
        if not notes_dir.exists():
            return ids
        for p in notes_dir.glob("*.jsonl"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            rid = rec.get("record_id", rec.get("note_id", ""))
                            if rid:
                                ids.add(rid)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue
        return ids

    @staticmethod
    def _make_zip(root: Path, dest: Path):
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in root.rglob("*"):
                if f.is_file():
                    arcname = str(f.relative_to(root))
                    zf.write(f, arcname)
