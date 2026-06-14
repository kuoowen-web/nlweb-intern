#!/usr/bin/env python3
"""
專案代碼索引工具 - 將 .py, .yaml, .md 檔案索引到 SQLite 資料庫

功能：
- 遞迴掃描專案目錄
- 過濾特定副檔名 (.py, .yaml, .md)
- 排除隱藏資料夾、.git、__pycache__、venv 等
- 使用 FTS5 實現高速全文搜尋
- 增量更新機制（只更新有變動的檔案）
- 自動清理已刪除的檔案記錄

Usage:
    python indexer.py --index              # 執行掃描並更新資料庫
    python indexer.py --search "關鍵字"    # 全文檢索
    python indexer.py --stats              # 顯示統計資訊
    python indexer.py --list-ext .py       # 列出特定副檔名的檔案
"""

import argparse
import io
import os
import sqlite3
import sys
from pathlib import Path
from typing import Generator, Optional

# 修復 Windows 終端機編碼問題
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 設定
DB_NAME = "project_index.db"
INCLUDE_EXTENSIONS = {".py", ".yaml", ".yml", ".md", ".js", ".css", ".html"}
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    "venv",
    "myenv",
    "myenv311",
    ".venv",
    "node_modules",
    ".vs",
    ".vscode",
    ".idea",
    "qdrant_data",
    ".ipynb_checkpoints",
    ".obsidian",
    ".claude",
}
EXCLUDE_PATTERNS = {".db", ".pyc", ".pyo"}


def get_db_path() -> Path:
    """取得資料庫路徑（專案根目錄下的 tools/project_index.db）"""
    script_dir = Path(__file__).parent
    return script_dir / DB_NAME


def get_project_root() -> Path:
    """取得專案根目錄"""
    return Path(__file__).parent.parent


def init_database(conn: sqlite3.Connection) -> None:
    """初始化資料庫結構"""
    cursor = conn.cursor()

    # 建立 files 主表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            extension TEXT,
            content TEXT,
            last_modified REAL,
            size INTEGER
        )
    """)

    # 檢查 FTS5 虛擬表是否存在
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name='files_fts'
    """)

    if not cursor.fetchone():
        # 建立 FTS5 虛擬表用於全文搜尋
        cursor.execute("""
            CREATE VIRTUAL TABLE files_fts USING fts5(
                path,
                content,
                content='files',
                content_rowid='rowid'
            )
        """)

        # 建立觸發器以同步 FTS 索引
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, path, content)
                VALUES (new.rowid, new.path, new.content);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, path, content)
                VALUES ('delete', old.rowid, old.path, old.content);
            END
        """)

        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, path, content)
                VALUES ('delete', old.rowid, old.path, old.content);
                INSERT INTO files_fts(rowid, path, content)
                VALUES (new.rowid, new.path, new.content);
            END
        """)

    conn.commit()


def should_exclude_dir(dir_name: str) -> bool:
    """檢查是否應該排除此目錄"""
    # 排除隱藏資料夾（以 . 開頭）
    if dir_name.startswith("."):
        return True
    # 排除特定資料夾
    return dir_name in EXCLUDE_DIRS


def should_include_file(file_path: Path) -> bool:
    """檢查檔案是否應該被索引"""
    # 檢查副檔名
    if file_path.suffix.lower() not in INCLUDE_EXTENSIONS:
        return False
    # 排除特定模式
    if file_path.suffix.lower() in EXCLUDE_PATTERNS:
        return False
    # 排除 .db 檔案
    if file_path.suffix == ".db":
        return False
    return True


def scan_files(root_dir: Path) -> Generator[Path, None, None]:
    """遞迴掃描目錄，產生符合條件的檔案路徑"""
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # 過濾要排除的目錄（修改 dirnames 會影響 os.walk 的遞迴）
        dirnames[:] = [d for d in dirnames if not should_exclude_dir(d)]

        for filename in filenames:
            file_path = Path(dirpath) / filename
            if should_include_file(file_path):
                yield file_path


def read_file_content(file_path: Path) -> Optional[str]:
    """讀取檔案內容，處理編碼問題"""
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]

    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            print(f"警告: 無法讀取 {file_path}: {e}")
            return None

    print(f"警告: 無法解碼 {file_path}")
    return None


def index_files(conn: sqlite3.Connection, root_dir: Path) -> dict:
    """索引檔案到資料庫，回傳統計資訊"""
    cursor = conn.cursor()
    stats = {"added": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 0}

    # 收集目前硬碟上存在的檔案路徑
    current_files = set()

    for file_path in scan_files(root_dir):
        rel_path = os.path.relpath(file_path, root_dir)
        current_files.add(rel_path)

        try:
            stat = file_path.stat()
            last_modified = stat.st_mtime
            size = stat.st_size

            # 檢查是否需要更新
            cursor.execute(
                "SELECT last_modified FROM files WHERE path = ?",
                (rel_path,)
            )
            row = cursor.fetchone()

            if row and row[0] >= last_modified:
                # 檔案沒有變動，跳過
                stats["skipped"] += 1
                continue

            # 讀取檔案內容
            content = read_file_content(file_path)
            if content is None:
                stats["errors"] += 1
                continue

            # 插入或更新記錄
            cursor.execute("""
                INSERT OR REPLACE INTO files (path, extension, content, last_modified, size)
                VALUES (?, ?, ?, ?, ?)
            """, (rel_path, file_path.suffix.lower(), content, last_modified, size))

            if row:
                stats["updated"] += 1
            else:
                stats["added"] += 1

        except Exception as e:
            print(f"錯誤: 處理 {file_path} 時發生問題: {e}")
            stats["errors"] += 1

    # 清理已刪除的檔案記錄
    cursor.execute("SELECT path FROM files")
    db_files = {row[0] for row in cursor.fetchall()}

    deleted_files = db_files - current_files
    for deleted_path in deleted_files:
        cursor.execute("DELETE FROM files WHERE path = ?", (deleted_path,))
        stats["deleted"] += 1

    conn.commit()
    return stats


def search_files(conn: sqlite3.Connection, query: str, limit: int = 20) -> list:
    """使用 FTS5 搜尋檔案"""
    cursor = conn.cursor()

    # 使用 FTS5 搜尋
    cursor.execute("""
        SELECT
            f.path,
            f.extension,
            f.size,
            snippet(files_fts, 1, '>>>', '<<<', '...', 50) as snippet
        FROM files_fts
        JOIN files f ON files_fts.rowid = f.rowid
        WHERE files_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit))

    return cursor.fetchall()


def get_statistics(conn: sqlite3.Connection) -> dict:
    """取得資料庫統計資訊"""
    cursor = conn.cursor()

    # 總檔案數
    cursor.execute("SELECT COUNT(*) FROM files")
    total_files = cursor.fetchone()[0]

    # 各副檔名統計
    cursor.execute("""
        SELECT extension, COUNT(*), SUM(size), SUM(length(content))
        FROM files
        GROUP BY extension
        ORDER BY COUNT(*) DESC
    """)
    by_extension = cursor.fetchall()

    # 總大小
    cursor.execute("SELECT SUM(size), SUM(length(content)) FROM files")
    total_size, total_content = cursor.fetchone()

    return {
        "total_files": total_files,
        "total_size": total_size or 0,
        "total_content_chars": total_content or 0,
        "by_extension": by_extension,
    }


def list_by_extension(conn: sqlite3.Connection, ext: str) -> list:
    """列出特定副檔名的檔案"""
    cursor = conn.cursor()

    if not ext.startswith("."):
        ext = "." + ext

    cursor.execute("""
        SELECT path, size, last_modified
        FROM files
        WHERE extension = ?
        ORDER BY path
    """, (ext.lower(),))

    return cursor.fetchall()


def format_size(size: int) -> str:
    """格式化檔案大小"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main():
    parser = argparse.ArgumentParser(
        description="專案代碼索引工具 - 將程式碼索引到 SQLite 以便快速搜尋"
    )
    parser.add_argument(
        "--index", "-i",
        action="store_true",
        help="執行掃描並更新資料庫"
    )
    parser.add_argument(
        "--search", "-s",
        type=str,
        metavar="QUERY",
        help="使用 FTS5 進行全文檢索"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="顯示資料庫統計資訊"
    )
    parser.add_argument(
        "--list-ext", "-l",
        type=str,
        metavar="EXT",
        help="列出特定副檔名的檔案 (例如: .py)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="搜尋結果數量限制 (預設: 20)"
    )

    args = parser.parse_args()

    # 如果沒有提供任何參數，顯示說明
    if not any([args.index, args.search, args.stats, args.list_ext]):
        parser.print_help()
        sys.exit(0)

    db_path = get_db_path()
    project_root = get_project_root()

    # 連接資料庫
    conn = sqlite3.connect(db_path)
    init_database(conn)

    try:
        if args.index:
            print(f"正在索引專案: {project_root}")
            print(f"資料庫位置: {db_path}")
            print("-" * 50)

            stats = index_files(conn, project_root)

            print(f"索引完成!")
            print(f"  新增: {stats['added']} 個檔案")
            print(f"  更新: {stats['updated']} 個檔案")
            print(f"  跳過: {stats['skipped']} 個檔案 (無變動)")
            print(f"  刪除: {stats['deleted']} 個記錄 (檔案已移除)")
            if stats['errors']:
                print(f"  錯誤: {stats['errors']} 個檔案")

        if args.search:
            results = search_files(conn, args.search, args.limit)

            if not results:
                print(f"找不到符合 '{args.search}' 的結果")
            else:
                print(f"搜尋 '{args.search}' 找到 {len(results)} 個結果:")
                print("-" * 50)

                for path, ext, size, snippet in results:
                    print(f"\n{path} ({ext}, {format_size(size)})")
                    if snippet:
                        # 清理並顯示片段
                        clean_snippet = snippet.replace("\n", " ").strip()
                        print(f"  {clean_snippet}")

        if args.stats:
            stats = get_statistics(conn)

            print("資料庫統計資訊")
            print("-" * 50)
            print(f"總檔案數: {stats['total_files']}")
            print(f"總檔案大小: {format_size(stats['total_size'])}")
            print(f"總內容字數: {stats['total_content_chars']:,}")
            print()
            print("依副檔名分類:")
            print(f"{'副檔名':<10} {'檔案數':>8} {'大小':>12} {'內容字數':>15}")
            print("-" * 50)

            for ext, count, size, content_len in stats["by_extension"]:
                print(f"{ext:<10} {count:>8} {format_size(size or 0):>12} {(content_len or 0):>15,}")

        if args.list_ext:
            files = list_by_extension(conn, args.list_ext)

            if not files:
                print(f"找不到副檔名為 {args.list_ext} 的檔案")
            else:
                print(f"副檔名 {args.list_ext} 的檔案 ({len(files)} 個):")
                print("-" * 50)

                for path, size, _ in files:
                    print(f"  {path} ({format_size(size)})")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
