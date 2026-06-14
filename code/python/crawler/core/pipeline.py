"""
pipeline.py - TSV 輸出管道

負責將爬取的資料以 TSV 格式寫入檔案，供 M0 Indexing Module 使用。
"""

import json
import asyncio
import aiofiles
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging
import time

from . import settings


class TSVWriter:
    """
    TSV 格式資料寫入器
    將爬取的資料以 TSV 格式寫入檔案
    格式：URL \t JSON_STRING

    支援自動切塊：
    - 按文章數量切塊（例如每 5000 篇一個檔案）
    - 按月份切塊（根據文章發布日期）
    """

    def __init__(
        self,
        source_name: str,
        output_dir: Optional[Path] = None,
        filename: Optional[str] = None,
        chunk_size: int = 0,
        chunk_by_month: bool = False
    ):
        """
        初始化 TSV 寫入器

        Args:
            source_name: 新聞來源名稱
            output_dir: 輸出目錄（可選，預設使用設定中的目錄）
            filename: 輸出檔案名稱（可選，預設使用時間戳）
            chunk_size: 每個檔案的最大文章數（0 表示不限制）
            chunk_by_month: 是否按文章發布月份分檔
        """
        self.source_name = source_name
        self.logger = logging.getLogger(self.__class__.__name__)

        # 設定輸出目錄
        self.output_dir = output_dir if output_dir else settings.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 切塊設定
        self.chunk_size = chunk_size
        self.chunk_by_month = chunk_by_month

        # 檔案狀態追蹤
        self.base_filename = filename
        self.current_chunk = 0
        self.current_month = None
        self.items_in_current_file = 0

        # 初始化檔案路徑
        self._init_output_path()

        # 設定鎖，防止多線程寫入衝突
        self.lock = asyncio.Lock()

        self.logger.info(f"TSVWriter initialized: {self.output_path}")
        if chunk_size > 0:
            self.logger.info(f"  Chunk size: {chunk_size} articles per file")
        if chunk_by_month:
            self.logger.info(f"  Chunk by month: enabled")

    def _init_output_path(self, month_str: Optional[str] = None) -> None:
        """Initialize or update the output file path"""
        if self.base_filename:
            base = self.base_filename.replace('.tsv', '')
        else:
            timestamp = time.strftime('%Y-%m-%d_%H-%M')
            base = f"{self.source_name}_{timestamp}"

        # 加上月份標記
        if self.chunk_by_month and month_str:
            base = f"{self.source_name}_{month_str}"

        # 加上 chunk 編號（如果需要）
        if self.chunk_size > 0 and not self.chunk_by_month:
            self.filename = f"{base}_part{self.current_chunk:03d}.tsv"
        else:
            self.filename = f"{base}.tsv"

        self.output_path = self.output_dir / self.filename

    def _extract_month(self, data: Dict[str, Any]) -> Optional[str]:
        """Extract month string (YYYY-MM) from article data"""
        date_str = data.get('datePublished')
        if not date_str:
            return None
        try:
            # Handle ISO format: 2025-01-15T10:30:00
            return date_str[:7]  # "2025-01"
        except Exception:
            return None

    async def _maybe_rotate_file(self, data: Dict[str, Any]) -> None:
        """Check if we need to rotate to a new file"""
        need_rotate = False
        new_month = None

        # Check chunk size limit
        if self.chunk_size > 0 and self.items_in_current_file >= self.chunk_size:
            need_rotate = True
            self.current_chunk += 1
            self.logger.info(f"Rotating file: chunk size {self.chunk_size} reached, starting part {self.current_chunk}")

        # Check month change
        if self.chunk_by_month:
            new_month = self._extract_month(data)
            if new_month and new_month != self.current_month:
                need_rotate = True
                self.current_month = new_month
                self.logger.info(f"Rotating file: new month {new_month}")

        if need_rotate:
            self.items_in_current_file = 0
            self._init_output_path(month_str=self.current_month)

    async def save_item(self, url: str, data: Dict[str, Any]) -> bool:
        """
        儲存單筆資料
        格式：URL \t JSON_STRING

        Args:
            url: 文章URL
            data: 文章資料字典

        Returns:
            成功返回True，失敗返回False
        """
        try:
            # 使用鎖確保寫入操作的原子性
            async with self.lock:
                # 檢查是否需要切換檔案
                await self._maybe_rotate_file(data)

                # 確保 JSON 字串使用 ASCII 編碼，中文轉為 Unicode escape
                json_str = json.dumps(
                    data,
                    ensure_ascii=settings.ENSURE_ASCII,
                    separators=(',', ':')
                )

                async with aiofiles.open(self.output_path, 'a', encoding='utf-8') as f:
                    await f.write(f"{url}\t{json_str}\n")

                self.items_in_current_file += 1

            return True

        except Exception as e:
            self.logger.error(f"Error saving data for {url}: {str(e)}")
            if settings.DEBUG:
                import traceback
                self.logger.error(traceback.format_exc())
            return False

    async def save_batch(self, data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        儲存多筆資料

        Args:
            data_list: 包含 {'url': ..., 'data': ...} 的列表

        Returns:
            包含成功和失敗計數的字典
        """
        results = {
            'total': len(data_list),
            'success': 0,
            'failed': 0,
            'failed_urls': []
        }

        for item in data_list:
            if 'url' not in item or 'data' not in item:
                self.logger.error(f"Invalid data format: {item}")
                results['failed'] += 1
                if 'url' in item:
                    results['failed_urls'].append(item['url'])
                continue

            success = await self.save_item(item['url'], item['data'])
            if success:
                results['success'] += 1
            else:
                results['failed'] += 1
                results['failed_urls'].append(item['url'])

        self.logger.info(
            f"Batch save completed: {results['success']}/{results['total']} successful"
        )
        if results['failed'] > 0:
            self.logger.warning(f"Failed to save {results['failed']} items")

        return results


class Pipeline:
    """
    資料處理管道
    協調資料的處理和存儲
    """

    def __init__(
        self,
        source_name: str,
        output_dir: Optional[Path] = None,
        filename: Optional[str] = None,
        chunk_size: int = 0,
        chunk_by_month: bool = False
    ):
        """
        初始化管道

        Args:
            source_name: 新聞來源名稱
            output_dir: 輸出目錄（可選）
            filename: 輸出檔案名稱（可選）
            chunk_size: 每個檔案的最大文章數（0 表示不限制）
            chunk_by_month: 是否按文章發布月份分檔
        """
        self.writer = TSVWriter(
            source_name,
            output_dir,
            filename,
            chunk_size=chunk_size,
            chunk_by_month=chunk_by_month
        )
        self.logger = logging.getLogger(self.__class__.__name__)

    async def process_and_save(self, url: str, data: Dict[str, Any]) -> bool:
        """
        處理並儲存單筆資料

        Args:
            url: 文章URL
            data: 文章資料

        Returns:
            成功返回True，失敗返回False
        """
        return await self.writer.save_item(url, data)

    async def process_and_save_batch(
        self,
        results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        處理並儲存多筆資料

        Args:
            results: 包含 {'url': ..., 'data': ...} 的列表

        Returns:
            包含成功和失敗計數的字典
        """
        return await self.writer.save_batch(results)
