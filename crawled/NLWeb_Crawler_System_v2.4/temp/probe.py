"""
probe.py - 自動化網站偵查腳本（改進版 v3）

Phase 0 - 戰場偵查 (Reconnaissance)

改進（v3）：
- 增加 URL 清理邏輯（處理相對路徑）
- 抓取所有文章連結（不限制 5 個）
- 修復防禦測試和內容驗證

執行方式：
python temp/probe.py
"""

import asyncio
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urljoin
from bs4 import BeautifulSoup

# ==================== HTTP 客戶端 ====================

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    print("⚠️  aiohttp 未安裝")

try:
    from curl_cffi.requests import AsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    print("⚠️  curl_cffi 未安裝 (pip install curl_cffi)")


# ==================== HTTP Headers ====================
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
}


# ==================== 自動化偵查工具 ====================

class AutoScout:
    """自動化偵查兵（改進版 v3）"""
    
    def __init__(self, home_url: str):
        self.home_url = home_url.rstrip('/')
        self.base_domain = self._extract_domain(home_url)
        
        self.report = {
            "home_url": home_url,
            "base_domain": self.base_domain,
            "categories": {},
            "subdomains": [],
            "list_page": None,
            "sample_articles": [],
            "url_pattern": None,
            "turbo_mode": False,
            "defense_test": {},
            "content_validation": {}
        }
    
    def _extract_domain(self, url: str) -> str:
        """提取主網域"""
        match = re.search(r'https?://(?:www\.)?([^/]+)', url)
        return match.group(1) if match else ""
    
    def _normalize_url(self, url: str, base_url: str) -> str:
        """
        清理和標準化 URL
        
        處理：
        - 相對路徑 (../, ./)
        - 絕對路徑 (/path)
        - 完整 URL (http://...)
        """
        # 使用 urljoin 自動處理相對路徑
        normalized = urljoin(base_url, url)
        return normalized
    
    # ==================== 階段 1：全自動偵查 ====================
    
    async def auto_reconnaissance(self):
        """全自動偵查流程"""
        print("\n" + "="*70)
        print("🤖 階段 2：全自動偵查")
        print("="*70)
        
        await self._analyze_homepage()
        await self._find_list_page()
        await self._extract_sample_articles()
        await self._analyze_url_pattern()
        await self._test_defenses()
        
        print("\n" + "="*70)
        print("✅ 自動偵查完成")
        print("="*70)
    
    async def _analyze_homepage(self):
        """分析首頁"""
        print("\n【步驟 1/5】分析首頁...")
        print("-" * 70)
        
        html = await self._fetch_html(self.home_url)
        
        if not html:
            print("❌ 無法抓取首頁")
            return
        
        print(f"✅ 成功抓取首頁 ({len(html)} bytes)")
        
        soup = BeautifulSoup(html, 'lxml')
        
        print("\n1️⃣ 尋找分類...")
        categories = self._find_categories(soup)
        
        if categories:
            self.report['categories'] = categories
            print(f"   ✅ 找到 {len(categories)} 個分類")
            for i, (code, info) in enumerate(list(categories.items())[:5], 1):
                print(f"      {i}. {info['name']:12s} -> {code}")
        else:
            print("   ⚠️  未找到分類")
        
        print("\n2️⃣ 檢查子網域...")
        subdomains = self._find_subdomains(soup)
        
        if subdomains:
            self.report['subdomains'] = list(subdomains)
            print(f"   ⚠️  發現 {len(subdomains)} 個子網域")
            for subdomain in list(subdomains)[:3]:
                print(f"      - {subdomain}")
        else:
            print("   ✅ 無子網域陷阱")
    
    async def _find_list_page(self):
        """尋找列表頁"""
        print("\n【步驟 2/5】尋找列表頁...")
        print("-" * 70)
        
        html = await self._fetch_html(self.home_url)
        
        if html:
            soup = BeautifulSoup(html, 'lxml')
            
            keywords = ['即時', '最新', '全部', 'latest', 'all', 'news', '新聞']
            for link in soup.find_all('a', href=True):
                text = link.get_text(strip=True).lower()
                href = link['href']
                
                if any(kw in text for kw in keywords):
                    # 使用 _normalize_url 處理相對路徑
                    href = self._normalize_url(href, self.home_url)
                    
                    if await self._validate_list_page(href):
                        self.report['list_page'] = href
                        print(f"✅ 找到列表頁: {href}")
                        print(f"   關鍵字: {text}")
                        return
        
        print("   嘗試常見路徑...")
        common_paths = [
            '/list/aall.aspx',
            '/list/all.aspx',
            '/list/',
            '/news/',
            '/latest/',
            '/all/',
            '/article/list/',
        ]
        
        for path in common_paths:
            test_url = self.home_url + path
            
            if await self._validate_list_page(test_url):
                self.report['list_page'] = test_url
                print(f"✅ 找到列表頁: {test_url}")
                return
        
        if self.report['categories']:
            print("   從分類中尋找...")
            for code, info in list(self.report['categories'].items())[:3]:
                url = info.get('url')
                if url and await self._validate_list_page(url):
                    self.report['list_page'] = url
                    print(f"✅ 找到列表頁: {url}")
                    print(f"   分類: {info['name']}")
                    return
        
        print("❌ 無法自動找到列表頁")
    
    async def _extract_sample_articles(self):
        """從列表頁提取範例文章（改進版：抓取所有連結）"""
        print("\n【步驟 3/5】提取範例文章...")
        print("-" * 70)
        
        if not self.report['list_page']:
            print("⚠️  沒有列表頁，無法提取範例")
            return
        
        html = await self._fetch_html(self.report['list_page'])
        
        if not html:
            print("❌ 無法抓取列表頁")
            return
        
        soup = BeautifulSoup(html, 'lxml')
        
        article_links = []
        seen_urls = set()  # 避免重複
        
        # 路徑格式模式
        path_patterns = [
            r'/news/[^/]+/\d+',
            r'/article/\d+',
            r'/\d+\.html',
        ]
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            
            # 檢查路徑格式
            if any(re.search(pattern, href) for pattern in path_patterns):
                # ✨ 使用 _normalize_url 清理 URL
                clean_url = self._normalize_url(href, self.report['list_page'])
                
                if clean_url not in seen_urls:
                    article_links.append(clean_url)
                    seen_urls.add(clean_url)
            
            # 檢查 Query String 格式
            elif self._has_article_id(href):
                # ✨ 使用 _normalize_url 清理 URL
                clean_url = self._normalize_url(href, self.report['list_page'])
                
                if clean_url not in seen_urls:
                    article_links.append(clean_url)
                    seen_urls.add(clean_url)
        
        if article_links:
            self.report['sample_articles'] = article_links
            print(f"✅ 提取 {len(article_links)} 個範例文章")
            
            # 顯示前 5 個範例
            display_count = min(5, len(article_links))
            for i, url in enumerate(article_links[:display_count], 1):
                print(f"   {i}. {url}")
            
            if len(article_links) > 5:
                print(f"   ... 還有 {len(article_links) - 5} 個")
        else:
            print("❌ 列表頁中沒有找到文章連結")
    
    def _has_article_id(self, url: str) -> bool:
        """
        檢查 URL 是否包含文章 ID
        
        支援格式：
        - Query String: ?news_id=123, ?id=123, ?article_id=123
        - 路徑: /news/123, /article/123
        """
        # 檢查 Query String
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        # 常見的 ID 參數名稱
        id_params = ['news_id', 'id', 'article_id', 'post_id', 'nid', 'aid']
        
        for param in id_params:
            if param in params:
                return True
        
        # 檢查路徑中的數字
        if re.search(r'/\d+', parsed.path):
            return True
        
        return False
    
    async def _analyze_url_pattern(self):
        """分析 URL 結構（改進版：支援 Query String）"""
        print("\n【步驟 4/5】分析 URL 結構...")
        print("-" * 70)
        
        if not self.report['sample_articles']:
            print("⚠️  沒有範例文章，無法分析 URL 結構")
            return
        
        print(f"分析 {len(self.report['sample_articles'])} 個範例...")
        
        patterns_found = []
        
        for url in self.report['sample_articles']:
            pattern = self._extract_url_pattern(url)
            if pattern:
                patterns_found.append(pattern)
        
        if not patterns_found:
            print("❌ 無法解析 URL 結構")
            return
        
        # 統計最常見的模式
        pattern_counts = {}
        for p in patterns_found:
            key = p['pattern']
            pattern_counts[key] = pattern_counts.get(key, 0) + 1
        
        most_common = max(pattern_counts, key=pattern_counts.get)
        
        print(f"✅ URL 結構: {most_common}")
        print(f"   匹配數量: {pattern_counts[most_common]}/{len(patterns_found)}")
        self.report['url_pattern'] = most_common
        
        # 提取所有 ID
        ids = [p.get('id') for p in patterns_found if p.get('id')]
        
        if ids:
            print(f"\n📊 ID 分析 ({len(ids)} 個):")
            
            # 顯示範例 ID
            display_count = min(5, len(ids))
            for i, id_val in enumerate(ids[:display_count], 1):
                print(f"   {i}. {id_val}")
            
            if len(ids) > 5:
                print(f"   ... 還有 {len(ids) - 5} 個")
            
            id_lengths = [len(str(id_val)) for id_val in ids]
            if len(set(id_lengths)) == 1:
                id_length = id_lengths[0]
                print(f"\n   ✅ ID 長度一致: {id_length} 位數")
                
                if id_length >= 8:
                    print(f"\n   嘗試從 ID 提取日期...")
                    date_success = 0
                    
                    for id_val in ids[:3]:
                        id_str = str(id_val)
                        date_part = id_str[:8]
                        
                        try:
                            date_obj = datetime.strptime(date_part, '%Y%m%d')
                            print(f"      ID {id_val}: {date_obj.strftime('%Y-%m-%d')} ✅")
                            date_success += 1
                        except ValueError:
                            print(f"      ID {id_val}: 無法解析日期 ❌")
                    
                    if date_success >= 2:
                        print(f"\n   ✅ 結論: ID 包含日期 (前 8 碼 = YYYYMMDD)")
                        print(f"   ✅ 可使用 Turbo Mode")
                        self.report['turbo_mode'] = True
                    else:
                        print(f"\n   ❌ ID 不包含日期")
                        self.report['turbo_mode'] = False
                else:
                    print(f"   ⚠️  ID 長度不足 8 位，無法提取日期")
                    print(f"   ❌ 無法使用 Turbo Mode")
                    self.report['turbo_mode'] = False
            else:
                print(f"   ⚠️  ID 長度不一致: {set(id_lengths)}")
                self.report['turbo_mode'] = False
    
    async def _test_defenses(self):
        """測試防火牆（加強版：含內容驗證）"""
        print("\n【步驟 5/5】測試防火牆...")
        print("-" * 70)
        
        if not self.report['sample_articles']:
            print("⚠️  沒有範例文章，無法測試")
            return
        
        # ✨ 使用清理後的 URL
        test_url = self.report['sample_articles'][0]
        print(f"測試 URL: {test_url}")
        print()
        
        results = {}
        successful_html = None
        successful_method = None
        
        # 測試 1: aiohttp
        print("【測試 1】aiohttp")
        if AIOHTTP_AVAILABLE:
            success, status, time_ms, html = await self._test_aiohttp_with_content(test_url)
            results['aiohttp'] = {
                'success': success,
                'status': status,
                'time_ms': time_ms,
                'content_length': len(html) if html else 0
            }
            
            if success:
                print(f"   ✅ 成功 | HTTP {status} | {time_ms:.0f}ms | {len(html)} bytes")
                successful_html = html
                successful_method = 'aiohttp'
            else:
                print(f"   ❌ 失敗 | HTTP {status}")
        else:
            print("   ⚠️  未安裝")
            results['aiohttp'] = {'success': False, 'status': 'NOT_INSTALLED'}
        
        # 測試 2: curl_cffi
        print("\n【測試 2】curl_cffi (模擬 Chrome 120)")
        if CURL_CFFI_AVAILABLE:
            success, status, time_ms, html = await self._test_curl_cffi_with_content(test_url)
            results['curl_cffi'] = {
                'success': success,
                'status': status,
                'time_ms': time_ms,
                'content_length': len(html) if html else 0
            }
            
            if success:
                print(f"   ✅ 成功 | HTTP {status} | {time_ms:.0f}ms | {len(html)} bytes")
                if not successful_html:
                    successful_html = html
                    successful_method = 'curl_cffi'
            else:
                print(f"   ❌ 失敗 | HTTP {status}")
        else:
            print("   ⚠️  未安裝 (pip install curl_cffi)")
            results['curl_cffi'] = {'success': False, 'status': 'NOT_INSTALLED'}
        
        # ✨ 內容驗證
        if successful_html:
            print("\n" + "="*70)
            print("📄 內容驗證")
            print("="*70)
            
            validation_result = self._validate_content(successful_html)
            self.report['content_validation'] = validation_result
            
            print(f"\n使用方法: {successful_method}")
            print(f"HTML 長度: {len(successful_html)} bytes")
            
            # 顯示 HTML 前 500 字元
            print(f"\n【HTML 前 500 字元】")
            print("-" * 70)
            preview = successful_html[:500]
            print(preview)
            if len(successful_html) > 500:
                print("...")
            
            # 顯示提取結果
            print(f"\n【提取測試】")
            print("-" * 70)
            
            if validation_result['title']:
                print(f"✅ 標題: {validation_result['title']}")
            else:
                print(f"❌ 無法提取標題")
            
            if validation_result['content_preview']:
                print(f"\n✅ 內文預覽 (前 200 字):")
                print(f"   {validation_result['content_preview']}")
            else:
                print(f"\n❌ 無法提取內文")
            
            if validation_result['date']:
                print(f"\n✅ 發布日期: {validation_result['date']}")
            
            # 錯誤頁面檢查
            print(f"\n【錯誤頁面檢查】")
            print("-" * 70)
            
            if validation_result['is_error_page']:
                print(f"❌ 這是錯誤頁面！")
                print(f"   原因: {validation_result['error_reason']}")
            else:
                print(f"✅ 不是錯誤頁面")
        
        # 結論
        print("\n" + "="*70)
        print("【結論】")
        print("="*70)
        
        if results.get('aiohttp', {}).get('success'):
            print("✅ 建議使用: AIOHTTP (無需特殊處理)")
            recommendation = "aiohttp"
        elif results.get('curl_cffi', {}).get('success'):
            print("⚠️  建議使用: CURL_CFFI (aiohttp 被擋)")
            recommendation = "curl_cffi"
        else:
            print("❌ 兩種方法都失敗，需要進一步調查")
            recommendation = "unknown"
        
        self.report['defense_test'] = {
            'test_url': test_url,
            'results': results,
            'recommendation': recommendation
        }
    
    def _validate_content(self, html: str) -> Dict:
        """驗證抓取到的內容"""
        soup = BeautifulSoup(html, 'lxml')
        
        result = {
            'title': None,
            'content_preview': None,
            'date': None,
            'is_error_page': False,
            'error_reason': None
        }
        
        # 1. 提取標題
        title_tags = [
            soup.find('h1'),
            soup.find('title'),
            soup.find('meta', {'property': 'og:title'}),
        ]
        
        for tag in title_tags:
            if tag:
                if tag.name == 'meta':
                    result['title'] = tag.get('content', '').strip()
                else:
                    result['title'] = tag.get_text(strip=True)
                
                if result['title']:
                    break
        
        # 2. 提取內文
        content_selectors = [
            'article',
            '.article-content',
            '.content',
            'div[itemprop="articleBody"]',
            '.post-content',
        ]
        
        for selector in content_selectors:
            content_tag = soup.select_one(selector)
            if content_tag:
                text = content_tag.get_text(strip=True)
                if len(text) > 50:
                    result['content_preview'] = text[:200]
                    break
        
        if not result['content_preview']:
            paragraphs = soup.find_all('p')
            all_text = ' '.join([p.get_text(strip=True) for p in paragraphs])
            if len(all_text) > 50:
                result['content_preview'] = all_text[:200]
        
        # 3. 提取日期
        date_selectors = [
            'time',
            '.date',
            '.publish-date',
            'meta[property="article:published_time"]',
            'span[itemprop="datePublished"]',
        ]
        
        for selector in date_selectors:
            date_tag = soup.select_one(selector)
            if date_tag:
                if date_tag.name == 'meta':
                    result['date'] = date_tag.get('content', '').strip()
                else:
                    result['date'] = date_tag.get_text(strip=True)
                
                if result['date']:
                    break
        
        # 4. 檢查是否為錯誤頁面
        error_keywords = [
            '404',
            'not found',
            '找不到',
            '頁面不存在',
            'error',
            '錯誤',
            '無法找到',
        ]
        
        page_text = soup.get_text().lower()
        
        for keyword in error_keywords:
            if keyword in page_text and len(page_text) < 5000:
                result['is_error_page'] = True
                result['error_reason'] = f"包含錯誤關鍵字: {keyword}"
                break
        
        if len(html) < 1000 and not result['content_preview']:
            result['is_error_page'] = True
            result['error_reason'] = "內容太短且無法提取文章內容"
        
        return result
    
    # ==================== 階段 2：缺漏補充 ====================
    
    async def fill_gaps(self):
        """補充缺漏資訊"""
        print("\n" + "="*70)
        print("🔧 階段 3：缺漏補充")
        print("="*70)
        
        gaps_found = False
        
        if not self.report['list_page']:
            print("\n⚠️  未找到列表頁")
            print("請提供列表頁 URL（即時新聞、最新新聞等）")
            print("範例: https://www.cna.com.tw/list/aall.aspx")
            
            list_url = input("列表頁 URL: ").strip()
            
            if list_url:
                if await self._validate_list_page(list_url):
                    self.report['list_page'] = list_url
                    print(f"✅ 列表頁已設定: {list_url}")
                    
                    await self._extract_sample_articles()
                    await self._analyze_url_pattern()
                else:
                    print("⚠️  列表頁可能無效")
            
            gaps_found = True
        
        if not self.report['sample_articles']:
            print("\n⚠️  未找到範例文章")
            print("請提供 3-5 個確定有內容的文章 URL")
            print("範例: https://www.cna.com.tw/news/aipl/202412290037.aspx")
            print("輸入完成後，直接按 Enter 結束")
            print()
            
            sample_urls = []
            for i in range(5):
                url = input(f"範例 {i+1}: ").strip()
                if not url:
                    if i >= 3:
                        break
                    else:
                        print(f"   ⚠️  至少需要 3 個範例")
                        continue
                sample_urls.append(url)
            
            if sample_urls:
                self.report['sample_articles'] = sample_urls
                print(f"✅ 已加入 {len(sample_urls)} 個範例")
                
                await self._analyze_url_pattern()
                await self._test_defenses()
            
            gaps_found = True
        
        if self.report['subdomains']:
            print(f"\n⚠️  發現 {len(self.report['subdomains'])} 個子網域:")
            for subdomain in self.report['subdomains'][:5]:
                print(f"   - {subdomain}")
            
            print("\n是否需要偵查子網域？(y/n)")
            answer = input("選擇: ").strip().lower()
            
            if answer == 'y':
                print("⚠️  子網域偵查功能尚未實作")
                print("提示: 可以手動將子網域當作新的首頁 URL 重新執行偵查")
            
            gaps_found = True
        
        if not gaps_found:
            print("\n✅ 無需補充，所有資訊已完整")
    
    # ==================== 輔助方法 ====================
    
    async def _fetch_html(self, url: str) -> Optional[str]:
        """抓取 HTML（優先使用 curl_cffi）"""
        if CURL_CFFI_AVAILABLE:
            try:
                async with AsyncSession() as session:
                    response = await session.get(
                        url,
                        headers=DEFAULT_HEADERS,
                        timeout=10,
                        impersonate="chrome120"
                    )
                    
                    if response.status_code == 200:
                        return response.text
            except Exception:
                pass
        
        if AIOHTTP_AVAILABLE:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url,
                        headers=DEFAULT_HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                        ssl=False
                    ) as response:
                        if response.status == 200:
                            return await response.text()
            except Exception:
                pass
        
        return None
    
    def _find_categories(self, soup: BeautifulSoup) -> Dict:
        """從首頁找分類"""
        categories = {}
        
        nav_links = (
            soup.select('nav a[href]') or
            soup.select('header a[href]') or
            soup.select('a[href*="/list/"]') or
            soup.select('a[href*="/category/"]')
        )
        
        for link in nav_links:
            href = link.get('href', '')
            text = link.get_text(strip=True)
            
            if not text or len(text) > 20:
                continue
            
            match = re.search(r'/(?:list|category)/([a-z0-9]+)', href)
            if match:
                code = match.group(1)
                
                # 使用 _normalize_url 處理相對路徑
                href = self._normalize_url(href, self.home_url)
                
                categories[code] = {
                    'name': text,
                    'url': href
                }
        
        return categories
    
    def _find_subdomains(self, soup: BeautifulSoup) -> set:
        """找子網域"""
        subdomains = set()
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            match = re.search(r'https?://([^/]+)', href)
            if match:
                subdomain = match.group(1)
                
                if (self.base_domain in subdomain and 
                    subdomain != self.base_domain and 
                    subdomain != f'www.{self.base_domain}'):
                    subdomains.add(subdomain)
        
        return subdomains
    
    async def _validate_list_page(self, url: str) -> bool:
        """驗證列表頁是否有效（改進版：支援 Query String）"""
        html = await self._fetch_html(url)
        
        if not html:
            return False
        
        soup = BeautifulSoup(html, 'lxml')
        
        article_links = soup.find_all('a', href=True)
        
        # 計算有效文章連結數量
        article_count = 0
        
        for link in article_links:
            href = link['href']
            
            # 檢查路徑格式
            if any(pattern in href for pattern in ['/news/', '/article/', '.html']):
                article_count += 1
            # 檢查 Query String 格式
            elif self._has_article_id(href):
                article_count += 1
            
            if article_count >= 5:
                return True
        
        return False
    
    def _extract_url_pattern(self, url: str) -> Optional[Dict]:
        """提取 URL 結構（改進版：支援 Query String）"""
        # 先嘗試 Query String 格式
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        
        # 檢查常見的 ID 參數
        id_params = ['news_id', 'id', 'article_id', 'post_id', 'nid', 'aid']
        
        for param in id_params:
            if param in params:
                id_value = params[param][0]
                
                # 構建模式描述
                pattern_desc = f"?{param}={{ID}}"
                
                # 提取其他參數
                other_params = {k: v[0] for k, v in params.items() if k != param}
                
                return {
                    'pattern': pattern_desc,
                    'id': id_value,
                    'params': other_params,
                    'url': url,
                    'type': 'query_string'
                }
        
        # 如果不是 Query String，嘗試路徑格式
        path_patterns = [
            (r'/news/([a-z]+)/(\d+)', 'category+id'),
            (r'/article/(\d+)', 'id_only'),
            (r'/(\d+)\.html', 'id_only'),
            (r'/([a-z]+)/(\d+)', 'category+id'),
        ]
        
        for pattern, pattern_type in path_patterns:
            match = re.search(pattern, url)
            if match:
                groups = match.groups()
                
                if pattern_type == 'category+id':
                    return {
                        'pattern': pattern,
                        'category': groups[0],
                        'id': groups[1],
                        'url': url,
                        'type': 'path'
                    }
                else:
                    return {
                        'pattern': pattern,
                        'id': groups[0],
                        'url': url,
                        'type': 'path'
                    }
        
        return None
    
    async def _test_aiohttp_with_content(self, url: str) -> Tuple[bool, int, float, Optional[str]]:
        """測試 aiohttp（返回 HTML）"""
        import time
        start = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=DEFAULT_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                    ssl=False
                ) as response:
                    elapsed = (time.time() - start) * 1000
                    
                    if response.status == 200:
                        html = await response.text()
                        return (len(html) > 1000, response.status, elapsed, html)
                    else:
                        return (False, response.status, elapsed, None)
        except Exception:
            elapsed = (time.time() - start) * 1000
            return (False, 0, elapsed, None)
    
    async def _test_curl_cffi_with_content(self, url: str) -> Tuple[bool, int, float, Optional[str]]:
        """測試 curl_cffi（返回 HTML）"""
        import time
        start = time.time()
        
        try:
            async with AsyncSession() as session:
                response = await session.get(
                    url,
                    headers=DEFAULT_HEADERS,
                    timeout=10,
                    impersonate="chrome120"
                )
                
                elapsed = (time.time() - start) * 1000
                
                if response.status_code == 200:
                    html = response.text
                    return (len(html) > 1000, response.status_code, elapsed, html)
                else:
                    return (False, response.status_code, elapsed, None)
        except Exception:
            elapsed = (time.time() - start) * 1000
            return (False, 0, elapsed, None)
    
    # ==================== 產出報告 ====================
    
    def generate_report(self):
        """產出最終報告"""
        print("\n" + "="*70)
        print("📋 最終偵查報告")
        print("="*70)
        
        print("\n【基本資訊】")
        print("-" * 70)
        print(f"首頁: {self.report['home_url']}")
        print(f"主網域: {self.report['base_domain']}")
        
        print("\n【任務 A：地形分析】")
        print("-" * 70)
        categories = self.report['categories']
        print(f"分類數量: {len(categories)}")
        if categories:
            print("主要分類:")
            for code, info in list(categories.items())[:5]:
                print(f"  - {info['name']:12s} ({code})")
        
        subdomains = self.report['subdomains']
        print(f"子網域: {'有 (' + str(len(subdomains)) + ' 個)' if subdomains else '無'}")
        
        print("\n【任務 B：水源定位】")
        print("-" * 70)
        list_page = self.report['list_page']
        if list_page:
            print(f"✅ 列表頁: {list_page}")
        else:
            print("❌ 未找到列表頁")
        
        print("\n【任務 C：暗號破解】")
        print("-" * 70)
        sample_count = len(self.report['sample_articles'])
        print(f"範例數量: {sample_count}")
        
        # 顯示範例 URL
        if sample_count > 0:
            display_count = min(5, sample_count)
            print(f"\n範例 URL (顯示前 {display_count} 個):")
            for i, url in enumerate(self.report['sample_articles'][:display_count], 1):
                print(f"  {i}. {url}")
            
            if sample_count > 5:
                print(f"  ... 還有 {sample_count - 5} 個")
        
        if self.report['url_pattern']:
            print(f"\nURL 結構: {self.report['url_pattern']}")
        else:
            print("\nURL 結構: 未解析")
        
        turbo = self.report['turbo_mode']
        print(f"Turbo Mode: {'✅ 可用' if turbo else '❌ 不可用'}")
        
        print("\n【任務 D：防禦測試】")
        print("-" * 70)
        defense = self.report['defense_test']
        
        if defense:
            results = defense.get('results', {})
            for method, result in results.items():
                status = "✅ 成功" if result.get('success') else "❌ 失敗"
                http_status = result.get('status', 'N/A')
                print(f"{method:12s}: {status} (HTTP {http_status})")
            
            recommendation = defense.get('recommendation', 'unknown')
            print(f"\n🎯 建議使用: {recommendation.upper()}")
        else:
            print("⚠️  未執行測試")
        
        # 內容驗證結果
        if self.report.get('content_validation'):
            print("\n【內容驗證】")
            print("-" * 70)
            validation = self.report['content_validation']
            
            if validation['title']:
                print(f"✅ 可提取標題")
            else:
                print(f"❌ 無法提取標題")
            
            if validation['content_preview']:
                print(f"✅ 可提取內文")
            else:
                print(f"❌ 無法提取內文")
            
            if validation['is_error_page']:
                print(f"❌ 內容驗證失敗: {validation['error_reason']}")
            else:
                print(f"✅ 內容驗證通過")
        
        # 儲存 JSON
        print("\n" + "="*70)
        report_file = "temp/probe_report.json"
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(self.report, f, ensure_ascii=False, indent=2)
            print(f"✅ 報告已儲存: {report_file}")
        except Exception as e:
            print(f"⚠️  無法儲存報告: {e}")
        
        print("="*70)
        print("偵查完成！")
        print("="*70)


# ==================== 主程式 ====================

async def main():
    """執行自動化偵查"""
    print("🕵️  自動化網站偵查工具（改進版 v3）")
    print("Phase 0 - 戰場偵查 (Reconnaissance)")
    print()
    print("改進:")
    print("  ✅ 支援 Query String 格式 (?news_id=123)")
    print("  ✅ 過濾列表頁，只保留文章頁")
    print("  ✅ 增加 URL 清理邏輯（處理相對路徑）")
    print("  ✅ 抓取所有文章連結（不限制數量）")
    print()
    print("⚠️  這是臨時偵查腳本，不會修改任何正式程式碼")
    print()
    
    if not CURL_CFFI_AVAILABLE and not AIOHTTP_AVAILABLE:
        print("❌ 錯誤: 至少需要安裝 aiohttp 或 curl_cffi")
        print("   pip install aiohttp")
        print("   pip install curl_cffi")
        return
    
    if not CURL_CFFI_AVAILABLE:
        print("⚠️  警告: curl_cffi 未安裝，部分網站可能無法抓取")
        print("   建議安裝: pip install curl_cffi")
        print()
    
    print("="*70)
    print("📝 階段 1：輸入首頁 URL")
    print("="*70)
    print()
    print("請輸入要偵查的網站首頁 URL")
    print("範例: https://www.cna.com.tw")
    print()
    
    home_url = input("首頁 URL: ").strip()
    
    if not home_url:
        print("❌ 首頁 URL 不可為空")
        return
    
    if not home_url.startswith('http'):
        print("❌ URL 必須以 http:// 或 https:// 開頭")
        return
    
    print(f"✅ 首頁: {home_url}")
    
    scout = AutoScout(home_url)
    
    await scout.auto_reconnaissance()
    await scout.fill_gaps()
    scout.generate_report()


if __name__ == "__main__":
    asyncio.run(main())
