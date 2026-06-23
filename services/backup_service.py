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
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    # ==================================================================
    # 导出
    # ==================================================================

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

            zip_path = self.backups_dir / f"autoread_books_backup_{_ts_tag()}.zip"
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

            zip_path = self.backups_dir / f"autoread_notes_backup_{_ts_tag()}.zip"
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

            # config snapshot (只读参考，不自动恢复)
            (root / "book_index.json").write_text(
                json.dumps(list(state.get("books", {}).values()), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            zip_path = self.backups_dir / f"autoread_full_backup_{_ts_tag()}.zip"
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

            if already_imported:
                preview["already_imported_backup"] = True
                preview["new_items"] = 0
                preview["skipped_existing_ids"] = preview["total_items"]
                preview["message"] = "该备份包已导入过，默认跳过。"
            else:
                preview["already_imported_backup"] = False
                preview["message"] = "可合并导入。已存在 ID 会自动跳过。" if preview["new_items"] > 0 else "所有记录均已存在，无需导入。"

            preview["backup_id"] = backup_id
            preview["backup_type"] = backup_type

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

        total = 0
        new_count = 0
        skip_count = 0
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
                if backup_type in ("books", "full"):
                    result.update(self._do_merge_books(zf))
                if backup_type in ("notes", "full"):
                    result.update(self._do_merge_notes(zf))

            result["status"] = "success"
            self._record_history(backup_id, backup_type, result)
            logger.info(f"[AutoRead Backup] Imported: {backup_id} ({backup_type}) +{result.get('imported_items', 0)}")
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

        for name in zf.namelist():
            if not name.startswith("books/") or name.endswith("/"):
                continue
            book_id = Path(name).stem
            dest = self.data_dir / name
            if dest.exists() or book_id in current_books:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))
            imported += 1

        # 还原 chunks
        for name in zf.namelist():
            if not name.startswith("chunks/") or name.endswith("/"):
                continue
            chunk_id = Path(name).stem
            dest = self.data_dir / name
            if dest.exists() or chunk_id in current_books:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))

        # 还原 book_index
        if "book_index.json" in zf.namelist():
            try:
                books_list = json.loads(zf.read("book_index.json").decode("utf-8"))
                full_state = {}
                if state.exists():
                    full_state = json.loads(state.read_text(encoding="utf-8"))
                full_state.setdefault("books", {})
                for b in books_list:
                    bid = b.get("book_id", "")
                    if bid and bid not in full_state["books"]:
                        full_state["books"][bid] = b
                tmp_path = state.with_suffix(".json.tmp")
                tmp_path.write_text(json.dumps(full_state, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp_path, state)
            except Exception:
                pass

        return {"imported_items": imported, "skipped_existing_ids": skipped}

    def _do_merge_notes(self, zf: zipfile.ZipFile) -> dict:
        existing = self._collect_existing_record_ids()
        imported = 0
        skipped = 0

        for name in zf.namelist():
            if not name.startswith("notes/") or not name.endswith(".jsonl"):
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
                            ids.add(rec.get("record_id", rec.get("note_id", "")))
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
