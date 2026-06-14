import json
import asyncio
import aiofiles
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging
import time

from config import settings

class TSVWriter:
    """
    TSV 格式資料寫入器
    參考舊版的 save_to_tsv_async 邏輯
    將爬取的資料以 TSV 格式寫入檔案
    """
    
    def __init__(self, source_name: str, output_dir: Optional[Path] = None, filename: Optional[str] = None):
        """
        初始化 TSV 寫入器
        
        Args:
            source_name: 新聞來源名稱
            output_dir: 輸出目錄（可選，預設使用設定中的目錄）
            filename: 輸出檔案名稱（可選，預設使用時間戳）
        """
        self.source_name = source_name
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 設定輸出目錄
        self.output_dir = output_dir if output_dir else settings.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 設定檔案名稱（參考舊版格式）
        if filename:
            self.filename = filename
        else:
            timestamp = time.strftime('%Y-%m-%d_%H-%M')
            self.filename = f"{source_name}_{timestamp}.tsv"
        
        self.output_path = self.output_dir / self.filename
        
        # 🆕 設定 ID 記錄檔路徑
        settings.CRAWLED_IDS_DIR.mkdir(parents=True, exist_ok=True)
        self.ids_filename = f"{source_name}.txt"
        self.ids_path = settings.CRAWLED_IDS_DIR / self.ids_filename
        
        # 設定鎖，防止多線程寫入衝突（參考舊版）
        self.lock = asyncio.Lock()
        # 🆕 ID 檔案專用鎖
        self.ids_lock = asyncio.Lock()
        
        self.logger.info(f"TSVWriter initialized: {self.output_path}")
        self.logger.info(f"ID tracker initialized: {self.ids_path}")
    
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
            # 確保JSON字串使用ASCII編碼，中文轉為Unicode escape
            # 參考舊版：ensure_ascii=True
            json_str = json.dumps(data, ensure_ascii=settings.ENSURE_ASCII, separators=(',', ':'))
            
            # 使用鎖確保寫入操作的原子性
            async with self.lock:
                # 使用 aiofiles 進行非同步寫入
                async with aiofiles.open(self.output_path, 'a', encoding='utf-8') as f:
                    # 格式：URL \t JSON_STRING \n
                    await f.write(f"{url}\t{json_str}\n")
            
            # 🆕 成功寫入資料後，記錄 ID
            await self._save_crawled_id(url)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving data for {url}: {str(e)}")
            if settings.DEBUG:
                import traceback
                self.logger.error(traceback.format_exc())
            return False
    
    async def _save_crawled_id(self, url: str) -> bool:
        """
        🆕 將已爬取的 URL 記錄到 ID 檔案
        
        Args:
            url: 文章 URL 或 ID
            
        Returns:
            成功返回 True，失敗返回 False
        """
        try:
            async with self.ids_lock:
                async with aiofiles.open(self.ids_path, 'a', encoding='utf-8') as f:
                    await f.write(f"{url}\n")
            return True
        except Exception as e:
            self.logger.error(f"Error saving crawled ID {url}: {str(e)}")
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
            # 確保每個項目都有 url 和 data 欄位
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
    
    def __init__(self, source_name: str, output_dir: Optional[Path] = None, filename: Optional[str] = None):
        """
        初始化管道
        
        Args:
            source_name: 新聞來源名稱
            output_dir: 輸出目錄（可選）
            filename: 輸出檔案名稱（可選）
        """
        self.writer = TSVWriter(source_name, output_dir, filename)
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
        # 這裡可以添加額外的資料處理邏輯
        # 例如：資料清洗、格式轉換等
        # 目前直接儲存
        
        return await self.writer.save_item(url, data)
    
    async def process_and_save_batch(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        處理並儲存多筆資料
        
        Args:
            results: 包含 {'url': ..., 'data': ...} 的列表
            
        Returns:
            包含成功和失敗計數的字典
        """
        return await self.writer.save_batch(results)
