import os
from pathlib import Path
import logging

# ==================== 專案目錄設定 ====================
BASE_DIR = Path(__file__).resolve().parent.parent

# 定義資料與日誌目錄
DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
OUTPUT_DIR = DATA_DIR / "articles"
CRAWLED_IDS_DIR = DATA_DIR / "crawled_ids"
TEMP_DIR = BASE_DIR / "temp"

# 確保目錄存在
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CRAWLED_IDS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 日誌設定 ====================
LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# ==================== HTTP 請求設定 ====================
REQUEST_TIMEOUT = 5
MAX_RETRIES = 2

# 重試延遲設定
RETRY_DELAY = 3.0
MAX_RETRY_DELAY = 60

# 併發控制
CONCURRENT_REQUESTS = 3
MIN_DELAY = 0.8
MAX_DELAY = 2.9

# 429 降速設定
RATE_LIMIT_COOLDOWN = 10.0
RATE_LIMIT_BACKOFF = 2.0

# 封鎖冷卻設定
BLOCKED_COOLDOWN = 20.0

# ==================== 預設 HTTP Headers ====================
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"macOS"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
}

# ==================== User-Agent 池 ====================
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

# ==================== Session 類型設定 ====================
# ✅ FIX #WORK-ORDER-803: 明確聲明 MOEA 不需要 curl_cffi
# ✅ FIX #WORK-ORDER-913: 新增 E-Info，建議開啟 curl_cffi
# 
# 說明：
# - cna, chinatimes: 需要 curl_cffi 繞過反爬蟲
# - einfo: 建議使用 curl_cffi（Drupal 網站可能有防護）
# - moea: 使用標準 aiohttp（政府網站通常無反爬蟲機制）
# 
# 注意：此列表用於 Engine 自動選擇 Session 類型
CURL_CFFI_SOURCES = [
    'cna',
    'chinatimes',
    'einfo',
    'esg_businesstoday',  # ✅ FIX #WORK-ORDER-923: 新增
]


# ==================== P0 新聞來源設定 ====================
SOURCES_P0 = {
    "ltn": {
        "name": "自由時報",
        "base_url": "https://news.ltn.com.tw",
        "domains": [
            {
                "name": "主站",
                "list_url": "https://news.ltn.com.tw/list/breakingnews",
                "article_pattern": "https://news.ltn.com.tw/news/life/breakingnews/{id}",
                "list_pattern": r'/news/\w+/breakingnews/(\d+)'
            },
            {
                "name": "健康",
                "list_url": "https://health.ltn.com.tw/breakingNews/9",
                "article_pattern": "https://health.ltn.com.tw/article/breakingnews/{id}",
                "list_pattern": r'/article/breakingnews/(\d+)'
            },
            {
                "name": "國防",
                "list_url": "https://def.ltn.com.tw/breakingnewslist",
                "article_pattern": "https://def.ltn.com.tw/article/breakingnews/{id}",
                "list_pattern": r'/article/breakingnews/(\d+)'
            },
            {
                "name": "財經",
                "list_url": "https://ec.ltn.com.tw/list/breakingnews",
                "article_pattern": "https://ec.ltn.com.tw/article/breakingnews/{id}",
                "list_pattern": r'/article/breakingnews/(\d+)'
            },
            {
                "name": "體育",
                "list_url": "https://sports.ltn.com.tw/breakingnews",
                "article_pattern": "https://sports.ltn.com.tw/news/breakingnews/{id}",
                "list_pattern": r'/news/breakingnews/(\d+)'
            },
            {
                "name": "藝文",
                "list_url": "https://art.ltn.com.tw/newslist",
                "article_pattern": "https://art.ltn.com.tw/article/breakingnews/{id}",
                "list_pattern": r'/article/breakingnews/(\d+)'
            }
        ]
    },
    "udn": {
        "name": "聯合報",
        "base_url": "https://udn.com",
        "breaknews_url": "https://udn.com/news/breaknews/1/99#breaknews",
        "categories": ['7328', '7266', '7251', '6656', '7270', '123', '1']
    }
}

# ==================== 輸出設定 ====================
OUTPUT_FORMAT = "tsv"
ENSURE_ASCII = True
MAX_ARTICLE_LENGTH = 20000

# ==================== 爬取模式設定 ====================
DEFAULT_MODE = "date"
DEFAULT_MAX_ARTICLES = 300
DEFAULT_DAYS_BACK = 3

# 停止條件
CONSECUTIVE_TOO_OLD_LIMIT = 30
CONSECUTIVE_FAIL_LIMIT = 50
MAX_CONSECUTIVE_MISSES = 100

# 智能跳躍設定
SMART_JUMP_THRESHOLD = 100
SMART_JUMP_ENABLED_SOURCES = ['chinatimes', 'cna']

# ==================== 調試設定 ====================
DEBUG = os.environ.get("NLWEB_DEBUG", "").lower() in ("true", "1", "yes")

# ==================== Schema.org 設定 ====================
SCHEMA_CONFIG = {
    'context': 'https://schema.org',
    'type': 'NewsArticle',
    'max_body_length': MAX_ARTICLE_LENGTH,
    'required_fields': [
        'headline', 
        'datePublished', 
        'articleBody', 
        '@type', 
        '@context'
    ]
}

# ==================== 新聞來源設定 ====================
# ✅ FIX #WORK-ORDER-803: 新增經濟部新聞來源設定
# ✅ FIX #WORK-ORDER-913: 新增環境資訊中心 (E-Info)
# ✅ FIX #WORK-ORDER-920: 調整 E-Info 防封鎖策略
NEWS_SOURCES = {
    "moea": {
        "name": "經濟部",
        "base_url": "https://www.moea.gov.tw",
        "list_url": "https://www.moea.gov.tw/Mns/populace/news/News.aspx?kind=1&menu_id=40&news_id=",
        "article_pattern": "https://www.moea.gov.tw/Mns/populace/news/News.aspx?kind=1&menu_id=40&news_id={id}",
        "id_type": "sequential",  # 流水號型 ID
        "description": "經濟部新聞發布",
        "encoding": "utf-8",
        "requires_curl_cffi": False,  # ✅ 明確聲明：不需要 curl_cffi
        "concurrent_limit": 5,  # 併發限制
        "delay_range": (1.0, 2.5),  # 延遲範圍（秒）
        "notes": "經濟部官方新聞發布系統，使用流水號型 ID，無反爬蟲機制"
    },
    
    # ✅ FIX #WORK-ORDER-913: 新增環境資訊中心 (E-Info)
    # ✅ FIX #WORK-ORDER-920: 調整防封鎖策略
    "einfo": {
        "name": "環境資訊中心",
        "base_url": "https://e-info.org.tw",
        "list_url": "https://e-info.org.tw/taxonomy/term/258/all",
        "article_pattern": "https://e-info.org.tw/node/{id}",
        "id_type": "sequential",  # 雖然是流水號，但我們會用列表策略
        "description": "環境資訊中心 - 環境新聞",
        "encoding": "utf-8",
        "requires_curl_cffi": True,  # 建議開啟，以防萬一
        
        # ✅ FIX #WORK-ORDER-920: 防封鎖策略調整
        "concurrent_limit": 1,  # ❌ 原本是 3，改為 1（單線程最安全）
        "delay_range": (5.0, 10.0),  # ❌ 原本是 (1.0, 3.0)，改慢一點（模擬人類閱讀）
        
        "notes": "Drupal CMS 網站，使用列表翻頁策略（List-Based Strategy），避免 Node ID 誤判。網站有反爬蟲機制，需使用單線程 + 長延遲。"
    },
    
    # ✅ FIX #WORK-ORDER-923: 新增今周刊 ESG 來源設定
    "esg_businesstoday": {
        "name": "今周刊 ESG",
        "base_url": "https://esg.businesstoday.com.tw",
        "list_url": "https://esg.businesstoday.com.tw/catalog/180686/",
        "article_pattern": "https://esg.businesstoday.com.tw/article/category/{cat_id}/post/{id}",
        "id_type": "list",  # 列表式爬取（透過 AJAX API）
        "description": "今周刊 ESG 永續台灣",
        "encoding": "utf-8",
        "requires_curl_cffi": True,  # 使用偽裝以利 AJAX 請求
        "concurrent_limit": 2,  # 避免對 API 造成壓力
        "delay_range": (1.5, 3.5),  # 延遲範圍（秒）
        "notes": "使用 AJAX 接口抓取動態列表，需要模擬瀏覽器行為"
    }
}
