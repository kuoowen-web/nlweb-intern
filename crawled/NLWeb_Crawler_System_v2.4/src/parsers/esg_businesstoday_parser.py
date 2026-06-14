"""
今周刊 ESG 解析器 (AJAX 策略)
=================================

✅ FIX #WORK-ORDER-926: 實作 List-Based Date Search（列表掃描日期搜尋）
✅ FIX #DATE-NAV-001: 修正 ID 位數問題（14碼 → 12碼）
✅ FIX #WORK-ORDER-925: 補上 get_date 方法（從 ID 提取日期）

策略：
1. 利用 AJAX 接口 (/catalog/{cat_id}/list/page/{page}/ajax) 進行列表爬取
2. 支援 5 大分類的多頁抓取
3. 嚴格遵守 Schema.org NewsArticle 格式
4. 整合 TextProcessor 進行文本清洗
5. 從文章 ID 直接提取日期（無需網路請求）
6. 自動修正 DateNavigator 傳入的 14 碼 ID
7. 支援日期範圍搜尋（列表掃描策略）

Author: Agent B
Date: 2026-01-01
Priority: P0 (Critical)
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests
from src.core.interfaces import BaseParser, SessionType
from src.utils.text_processor import TextProcessor


class EsgBusinessTodayParser(BaseParser):
    """
    今周刊 ESG 解析器
    
    技術特點：
    - 使用 AJAX 接口繞過滾動載入限制
    - curl_cffi 偽裝請求繞過 WAF
    - 嚴格 Schema.org NewsArticle 格式
    - 智能文本清洗與摘要提取
    - 從文章 ID 直接提取日期（高效能）
    - 自動修正 ID 位數（14碼 → 12碼）
    - 列表掃描日期搜尋（List-Based Date Search）
    """
    
    BASE_URL = "https://esg.businesstoday.com.tw"
    
    # 主要分類 ID (根據官方導航列)
    CATEGORIES = {
        180686: "全部",
        180687: "E永續環境",
        180688: "S社會責任",
        180689: "G公司治理",
        190807: "ESG快訊"
    }

    preferred_session_type = SessionType.CURL_CFFI

    def __init__(self, count: Optional[int] = None, **kwargs):
        super().__init__()
        self.count = count or 50
        self._discovered_ids = []
        self._id_to_url_map = {}  # 儲存 ID 與完整 URL 的對應

    @property
    def source_name(self) -> str:
        return "esg_businesstoday"

    async def get_latest_id(self) -> Optional[int]:
        """取得最新文章 ID"""
        ids = self.get_discovered_ids()
        return ids[0] if ids else None

    async def get_date(self, article_id: int) -> Optional[datetime]:
        """
        從文章 ID 提取發布日期
        
        今周刊 ESG 的文章 ID 格式：YYYYMMDDXXXX (12碼)
        - 前 8 位：日期 (YYYYMMDD)
        - 後 4 位：流水號
        
        ⚠️ 重要：DateNavigator 可能傳入 14 碼 ID，需自動修正
        
        範例：
        - 202512310016 (12碼) → 2025-12-31 ✅
        - 20251231001600 (14碼) → 自動修正為 202512310016 → 2025-12-31 ✅
        
        Args:
            article_id: 文章 ID (12碼或14碼)
            
        Returns:
            datetime 物件，若解析失敗則返回 None
        """
        try:
            # ✅ [FIX] 位數修正：處理 DateNavigator 傳入的 14 碼 ID
            id_str = str(article_id)
            
            if len(id_str) == 14:
                # 去掉最後兩碼，變回 12 碼
                id_str = id_str[:-2]
                self._logger.debug(f"ID 位數修正: {article_id} (14碼) → {id_str} (12碼)")
            
            if len(id_str) < 8:
                self._logger.warning(f"Invalid article ID format: {article_id}")
                return None
            
            # 提取日期部分 (YYYYMMDD)
            date_str = id_str[:8]
            
            # 解析為 datetime
            date_obj = datetime.strptime(date_str, '%Y%m%d')
            
            return date_obj
            
        except ValueError as e:
            self._logger.error(f"Failed to parse date from ID {article_id}: {e}")
            return None
        except Exception as e:
            self._logger.error(f"Unexpected error in get_date for ID {article_id}: {e}")
            return None

    def _parse_date_from_id(self, article_id: int) -> Optional[datetime]:
        """
        內部輔助：直接從 ID 解析日期（同步方法）
        
        ✅ FIX #WORK-ORDER-926: 新增方法
        
        Args:
            article_id: 文章 ID (12碼或14碼)
            
        Returns:
            datetime 物件，若解析失敗則返回 None
        """
        try:
            id_str = str(article_id)
            
            # 位數修正
            if len(id_str) == 14:
                id_str = id_str[:-2]
            
            if len(id_str) >= 8:
                date_str = id_str[:8]
                return datetime.strptime(date_str, '%Y%m%d')
        except (ValueError, IndexError):
            pass
        return None

    def _fetch_ids_from_page(self, url: str) -> List[int]:
        """
        內部輔助：抓取單頁的所有 ID
        
        ✅ FIX #WORK-ORDER-926: 新增方法
        
        Args:
            url: 列表頁 URL
            
        Returns:
            文章 ID 列表
        """
        ids = []
        try:
            r = requests.get(
                url, 
                impersonate="chrome110", 
                timeout=10,
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                }
            )
            
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'lxml')
                links = soup.select('a.article__item, a.hover-area, a[href*="/post/"]')
                
                for link in links:
                    href = link.get('href', '')
                    match = re.search(r'/post/(\d+)', href)
                    if match:
                        article_id = int(match.group(1))
                        ids.append(article_id)
                        
                        # 儲存完整 URL
                        if article_id not in self._id_to_url_map:
                            if href.startswith('/'):
                                full_url = self.BASE_URL + href
                            else:
                                full_url = href
                            self._id_to_url_map[article_id] = full_url
        except Exception as e:
            self._logger.debug(f"Error fetching page {url}: {e}")
        
        return ids

    def get_ids_by_date_range(self, start_date: datetime, end_date: datetime) -> List[int]:
        """
        [列表掃描策略] 根據日期範圍搜尋文章 ID
        
        ✅ FIX #WORK-ORDER-926: 核心方法
        
        邏輯：
        1. 掃描所有分類列表
        2. 由新到舊檢查文章日期
        3. 一旦遇到比 start_date 還舊的文章，就停止該分類的掃描
        4. 收集所有符合範圍的 ID
        
        Args:
            start_date: 開始日期
            end_date: 結束日期
            
        Returns:
            符合日期範圍的文章 ID 列表（降序排列）
        """
        found_ids = set()
        print(f"🔍 [ESG] Scanning lists for date range: {start_date.date()} ~ {end_date.date()}")
        
        for cat_id, cat_name in self.CATEGORIES.items():
            print(f"  📂 Scanning category: {cat_name} ({cat_id})...")
            page = 1
            stop_category = False
            
            while not stop_category:
                # 構建 URL (第1頁靜態，第2頁+ AJAX)
                if page == 1:
                    url = f"{self.BASE_URL}/catalog/{cat_id}/"
                else:
                    url = f"{self.BASE_URL}/catalog/{cat_id}/list/page/{page}/ajax"
                
                # 抓取並解析 ID
                page_ids = self._fetch_ids_from_page(url)
                
                if not page_ids:
                    print(f"    - Page {page}: No articles found, stopping category.")
                    break  # 沒資料了，換下一個分類
                
                # 檢查這一頁的所有 ID
                valid_count_in_page = 0
                for aid in page_ids:
                    # 使用同步方法解析日期
                    adate = self._parse_date_from_id(aid)
                    
                    if not adate:
                        continue
                    
                    if adate > end_date:
                        continue  # 太新，繼續找下一篇
                    
                    if adate < start_date:
                        # 發現文章比開始日期還舊，停止該分類
                        stop_category = True
                        break
                    
                    # 符合範圍
                    found_ids.add(aid)
                    valid_count_in_page += 1
                
                print(f"    - Page {page}: Found {valid_count_in_page} matching articles.")
                
                if stop_category:
                    print(f"    ⏹️  Reached older articles. Stopping category {cat_name}.")
                    break
                
                page += 1
                time.sleep(0.5)  # 禮貌性延遲
                
                # 安全閥：避免無窮迴圈
                if page > 50:
                    print("    ⚠️  Page limit reached.")
                    break
        
        sorted_ids = sorted(list(found_ids), reverse=True)
        print(f"✅ [ESG] Total found: {len(sorted_ids)} unique articles in range.")
        return sorted_ids

    def get_discovered_ids(self) -> List[int]:
        """
        利用 AJAX 接口抓取多頁文章 ID
        
        策略：
        1. 第 1 頁：靜態 HTML (/catalog/{cat_id}/)
        2. 第 2-N 頁：AJAX 接口 (/catalog/{cat_id}/list/page/{page}/ajax)
        3. 每個分類抓取 3 頁 (約 50-60 篇文章)
        """
        found_ids = set()
        
        for cat_id, cat_name in self.CATEGORIES.items():
            print(f"\n📂 掃描分類: {cat_name} (ID: {cat_id})")
            
            # 1. 抓取第 1 頁 (靜態)
            url_p1 = f"{self.BASE_URL}/catalog/{cat_id}/"
            page_ids = self._fetch_ids_from_page(url_p1)
            found_ids.update(page_ids)
            print(f"  ✅ 第 1 頁：新增 {len(page_ids)} 篇")
            
            # 2. 抓取第 2-3 頁 (AJAX)
            for page in range(2, 4):
                url_ajax = f"{self.BASE_URL}/catalog/{cat_id}/list/page/{page}/ajax"
                page_ids = self._fetch_ids_from_page(url_ajax)
                found_ids.update(page_ids)
                print(f"  ✅ 第 {page} 頁：新增 {len(page_ids)} 篇")
                time.sleep(0.5)  # 禮貌性延遲
                
                # 提前終止條件
                if len(found_ids) >= self.count * 1.5:
                    print(f"✅ 已收集足夠文章 ({len(found_ids)} 篇)")
                    break
            
            time.sleep(1)  # 分類間延遲
        
        # 轉為列表並排序 (新到舊)
        sorted_ids = sorted(list(found_ids), reverse=True)
        self._discovered_ids = sorted_ids[:self.count]
        
        print(f"\n✅ 總共發現 {len(sorted_ids)} 篇文章，保留前 {len(self._discovered_ids)} 篇")
        return self._discovered_ids

    def get_url(self, article_id: int) -> str:
        """
        構建文章 URL (含位數自動修正)
        
        ✅ [FIX #DATE-NAV-001] 位數修正邏輯
        """
        # 1. 優先使用快取
        if article_id in self._id_to_url_map:
            return self._id_to_url_map[article_id]
            
        # 2. ✅ [FIX] 位數修正：處理 DateNavigator 傳入的 14 碼 ID
        id_str = str(article_id)
        if len(id_str) == 14:
            article_id = int(id_str[:-2])
            self._logger.debug(f"URL 位數修正: {id_str} (14碼) → {article_id} (12碼)")
            
        # 3. 備用方案：使用 "全部" 分類
        return f"{self.BASE_URL}/article/category/180686/post/{article_id}"

    async def parse(self, html: str, url: str) -> Optional[Dict[str, Any]]:
        """
        解析單篇文章
        
        嚴格遵守 Schema.org NewsArticle 格式
        所有欄位必須是純字串或字串列表，不得為物件
        """
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # ========== 1. 標題 (headline) ==========
            headline = None
            
            # 方法 1: h1 標籤
            h1_tag = soup.select_one('div.content_top h1, h1')
            if h1_tag:
                headline = TextProcessor.clean_text(h1_tag.get_text())
            
            # 方法 2: og:title
            if not headline:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    headline = TextProcessor.clean_text(og_title.get('content', ''))
                    headline = headline.replace('－ESG永續台灣', '').strip()
            
            if not headline:
                print(f"⚠️ 無法提取標題: {url}")
                return None
            
            # ========== 2. 內文 (articleBody) ==========
            article_body = ""
            
            # 主要內文區域
            content_div = soup.select_one('div[itemprop="articleBody"]')
            if content_div:
                paragraphs = []
                for p in content_div.find_all(['p', 'h2', 'h3']):
                    text = TextProcessor.clean_text(p.get_text())
                    if text and len(text) > 10:
                        paragraphs.append(text)
                
                # ✅ FIX #WORK-ORDER-926: 移除 max_length 參數
                article_body = TextProcessor.smart_extract_summary(paragraphs)
            
            # 補充：摘要區域
            summary_div = soup.select_one('div.articlemark p')
            if summary_div:
                summary_text = TextProcessor.clean_text(summary_div.get_text())
                if summary_text and summary_text not in article_body:
                    article_body = summary_text + "\n\n" + article_body
            
            if not article_body:
                print(f"⚠️ 無法提取內文: {url}")
                return None
            
            # ========== 3. 作者 (author) ==========
            author = "今周刊"
            
            author_section = soup.select_one('div.author_left')
            if author_section:
                author_text = author_section.get_text()
                match = re.search(r'撰文：\s*(.+?)(?:\s|&nbsp;|分類：)', author_text)
                if match:
                    raw_author = match.group(1).strip()
                    author = TextProcessor.clean_author(raw_author)
            
            if author == "今周刊":
                meta_author = soup.find('meta', attrs={'name': 'author'})
                if meta_author:
                    author = TextProcessor.clean_author(meta_author.get('content', ''))
            
            if not isinstance(author, str):
                author = "今周刊"
            
            # ========== 4. 發布日期 (datePublished) ==========
            date_published = None
            
            if author_section:
                date_match = re.search(r'日期：(\d{4}-\d{2}-\d{2})', author_section.get_text())
                if date_match:
                    date_str = date_match.group(1)
                    try:
                        dt = datetime.strptime(date_str, '%Y-%m-%d')
                        date_published = dt.isoformat()
                    except:
                        pass
            
            if not date_published:
                url_match = re.search(r'/post/(\d{8})', url)
                if url_match:
                    date_str = url_match.group(1)
                    try:
                        dt = datetime.strptime(date_str, '%Y%m%d')
                        date_published = dt.isoformat()
                    except:
                        pass
            
            if not date_published:
                date_published = datetime.now().isoformat()
            
            # ========== 5. 分類 (keywords) ==========
            keywords = []
            
            meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
            if meta_keywords:
                kw_text = meta_keywords.get('content', '')
                keywords = [k.strip() for k in kw_text.split(',') if k.strip()]
            
            breadcrumb = soup.select_one('div.esg-breadcrumb')
            if breadcrumb:
                for link in breadcrumb.find_all('a'):
                    cat_name = TextProcessor.clean_text(link.get_text())
                    if cat_name and cat_name != '首頁' and cat_name not in keywords:
                        keywords.append(cat_name)
            
            keywords = [str(k) for k in keywords if k]
            
            # ========== 6. 圖片來源 ==========
            image_source = None
            if author_section:
                img_match = re.search(r'圖檔來源：(.+?)(?:日期：|$)', author_section.get_text())
                if img_match:
                    image_source = TextProcessor.clean_text(img_match.group(1))
            
            # ========== 7. 主圖 URL ==========
            image_url = None
            main_img = soup.select_one('div.content_top img')
            if main_img:
                image_url = main_img.get('src', '')
                if image_url and not image_url.startswith('http'):
                    image_url = self.BASE_URL + image_url
            
            # ========== 組合結果 ==========
            result = {
                "@type": "NewsArticle",
                "headline": headline,
                "articleBody": article_body,
                "author": author,
                "publisher": "今周刊 ESG",
                "datePublished": date_published,
                "inLanguage": "zh-TW",
                "url": url,
                "keywords": keywords,
            }
            
            if image_source:
                result["imageSource"] = image_source
            if image_url:
                result["imageUrl"] = image_url
            
            return result
            
        except Exception as e:
            print(f"❌ 解析錯誤 ({url}): {e}")
            import traceback
            traceback.print_exc()
            return None


# ========== 測試程式碼 ==========
if __name__ == "__main__":
    import asyncio
    from datetime import timedelta
    
    async def test_parser():
        """測試 EsgBusinessTodayParser（含日期範圍搜尋）"""
        print("="*80)
        print("🧪 今周刊 ESG Parser 測試（列表搜尋版）")
        print("="*80)
        
        parser = EsgBusinessTodayParser(count=10)
        
        # 測試 1：日期範圍搜尋
        print("\n【測試 1】日期範圍搜尋（List-Based Date Search）")
        print("-"*80)
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)  # 最近 7 天
        
        print(f"搜尋範圍: {start_date.date()} ~ {end_date.date()}")
        
        ids = parser.get_ids_by_date_range(start_date, end_date)
        
        print(f"\n✅ 找到 {len(ids)} 篇文章")
        if ids:
            print(f"最新 5 篇 ID: {ids[:5]}")
            
            # 驗證日期
            print("\n驗證日期：")
            for article_id in ids[:5]:
                date = parser._parse_date_from_id(article_id)
                if date:
                    print(f"  ID {article_id} → {date.strftime('%Y-%m-%d')}")
        
        # 測試 2：單篇解析
        if ids:
            print("\n【測試 2】單篇文章解析")
            print("-"*80)
            test_id = ids[0]
            test_url = parser.get_url(test_id)
            print(f"測試文章: {test_url}")
            
            r = requests.get(test_url, impersonate="chrome110", timeout=15)
            if r.status_code == 200:
                result = await parser.parse(r.text, test_url)
                
                if result:
                    print("\n✅ 解析成功！")
                    print(f"標題: {result['headline']}")
                    print(f"作者: {result['author']}")
                    print(f"日期: {result['datePublished']}")
                    print(f"分類: {', '.join(result['keywords'])}")
                    print(f"內文長度: {len(result['articleBody'])} 字")
                else:
                    print("❌ 解析失敗")
            else:
                print(f"❌ HTTP {r.status_code}")
        
        print("\n" + "="*80)
        print("✅ 測試完成（列表搜尋已驗證）")
        print("="*80)
    
    asyncio.run(test_parser())
