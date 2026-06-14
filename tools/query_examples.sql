-- =============================================================================
-- 專案索引資料庫 - 常用查詢範例
-- 使用方式: sqlite3 project_index.db < query_examples.sql
-- 或在 sqlite3 shell 中執行個別查詢
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. 基本統計查詢
-- -----------------------------------------------------------------------------

-- 總檔案數與總大小
SELECT
    COUNT(*) as total_files,
    SUM(size) as total_bytes,
    ROUND(SUM(size) / 1024.0 / 1024.0, 2) as total_mb,
    SUM(length(content)) as total_chars
FROM files;

-- 各副檔名統計
SELECT
    extension,
    COUNT(*) as file_count,
    ROUND(SUM(size) / 1024.0, 2) as total_kb,
    ROUND(AVG(size) / 1024.0, 2) as avg_kb
FROM files
GROUP BY extension
ORDER BY file_count DESC;

-- Python 檔案總行數估計 (假設平均每行 50 字元)
SELECT
    COUNT(*) as python_files,
    SUM(length(content)) as total_chars,
    SUM(length(content) - length(replace(content, char(10), ''))) as estimated_lines
FROM files
WHERE extension = '.py';

-- -----------------------------------------------------------------------------
-- 2. 檔案搜尋查詢
-- -----------------------------------------------------------------------------

-- 最大的 10 個檔案
SELECT
    path,
    extension,
    ROUND(size / 1024.0, 2) as size_kb
FROM files
ORDER BY size DESC
LIMIT 10;

-- 最近修改的 10 個檔案
SELECT
    path,
    datetime(last_modified, 'unixepoch', 'localtime') as modified_time
FROM files
ORDER BY last_modified DESC
LIMIT 10;

-- 搜尋特定目錄下的檔案
SELECT path, extension, size
FROM files
WHERE path LIKE 'code/python/core/%'
ORDER BY path;

-- 搜尋檔名包含特定關鍵字的檔案
SELECT path, extension, size
FROM files
WHERE path LIKE '%handler%'
ORDER BY path;

-- -----------------------------------------------------------------------------
-- 3. FTS5 全文搜尋查詢 (毫秒級搜尋)
-- -----------------------------------------------------------------------------

-- 基本關鍵字搜尋
SELECT f.path, f.extension, f.size
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'database'
ORDER BY rank
LIMIT 20;

-- 搜尋類別定義 (class ClassName)
SELECT f.path, f.extension
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'class AND Handler'
ORDER BY rank
LIMIT 20;

-- 搜尋函數定義 (def function_name)
SELECT f.path, f.extension
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'def AND async'
ORDER BY rank
LIMIT 20;

-- 搜尋 import 語句
SELECT f.path, f.extension
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'from AND import AND aiohttp'
ORDER BY rank
LIMIT 20;

-- 搜尋配置相關內容 (在 YAML 檔案中)
SELECT f.path
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'timeout OR retry OR connection'
  AND f.extension IN ('.yaml', '.yml')
ORDER BY rank;

-- 帶有片段預覽的搜尋
SELECT
    f.path,
    f.extension,
    snippet(files_fts, 1, '>>>', '<<<', '...', 30) as snippet
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'error AND handling'
ORDER BY rank
LIMIT 10;

-- -----------------------------------------------------------------------------
-- 4. 進階分析查詢
-- -----------------------------------------------------------------------------

-- 找出可能的重複檔案 (相同大小)
SELECT size, GROUP_CONCAT(path, ', ') as files, COUNT(*) as count
FROM files
GROUP BY size
HAVING COUNT(*) > 1
ORDER BY size DESC
LIMIT 20;

-- 統計每個目錄的檔案數
SELECT
    SUBSTR(path, 1, INSTR(path || '/', '/') - 1) as top_dir,
    COUNT(*) as file_count,
    ROUND(SUM(size) / 1024.0, 2) as total_kb
FROM files
GROUP BY top_dir
ORDER BY file_count DESC;

-- 搜尋 TODO 或 FIXME 註解
SELECT f.path, f.extension
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'TODO OR FIXME OR HACK'
ORDER BY rank;

-- 搜尋 API 端點定義
SELECT f.path
FROM files_fts
JOIN files f ON files_fts.rowid = f.rowid
WHERE files_fts MATCH 'route OR endpoint OR @app'
  AND f.extension = '.py'
ORDER BY rank;

-- -----------------------------------------------------------------------------
-- 5. 維護查詢
-- -----------------------------------------------------------------------------

-- 檢查 FTS 索引健康度
SELECT COUNT(*) as fts_records FROM files_fts;
SELECT COUNT(*) as file_records FROM files;

-- 重建 FTS 索引 (如果需要)
-- INSERT INTO files_fts(files_fts) VALUES('rebuild');

-- 優化 FTS 索引
-- INSERT INTO files_fts(files_fts) VALUES('optimize');
