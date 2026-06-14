"""
settings.py - 爬蟲設定模組

集中管理爬蟲相關設定，支援從 config/config_crawler.yaml 讀取。
"""

import os
from pathlib import Path
import logging

# ==================== 專案目錄設定 ====================
# 以 nlweb 專案根目錄為基準
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent  # nlweb/
CODE_DIR = BASE_DIR / "code" / "python" / "crawler"

# 定義資料與日誌目錄
DATA_DIR = BASE_DIR / "data" / "crawler"
LOG_DIR = DATA_DIR / "logs"
OUTPUT_DIR = DATA_DIR / "articles"
CRAWLED_IDS_DIR = DATA_DIR / "crawled_ids"

# 確保目錄存在
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CRAWLED_IDS_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 日誌設定 ====================
LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# ==================== HTTP 請求設定 ====================
REQUEST_TIMEOUT = 10
MAX_RETRIES = 2

# SSL 驗證設定
# 生產環境建議設為 True，開發/測試環境可設為 False
SSL_VERIFY = os.environ.get("CRAWLER_SSL_VERIFY", "true").lower() in ("true", "1", "yes")

# SSL 憑證路徑（可選，用於自訂 CA 憑證）
SSL_CA_BUNDLE = os.environ.get("CRAWLER_SSL_CA_BUNDLE", None)

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

# ==================== AutoThrottle 設定 ====================
# Scrapy 風格自適應延遲：根據伺服器回應速度動態調速
AUTOTHROTTLE_ENABLED = True                # 開關（False = 回退到固定 random delay）
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0      # 目標並行數（越高 → delay 越低）

# ==================== 預設 HTTP Headers ====================
DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
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
# 需要 curl_cffi 繞過反爬蟲的來源
CURL_CFFI_SOURCES = [
    'cna',
    'chinatimes',
    'einfo',
    'esg_businesstoday',
    'moea',
]

# ==================== 新聞來源設定 ====================
NEWS_SOURCES = {
    "ltn": {
        "name": "自由時報",
        "concurrent_limit": 5,
        "delay_range": (0.5, 1.5),
    },
    "udn": {
        "name": "聯合報",
        "concurrent_limit": 5,
        "delay_range": (0.5, 1.5),
    },
    "cna": {
        "name": "中央社",
        "concurrent_limit": 4,
        "delay_range": (0.8, 2.0),
    },
    "moea": {
        "name": "經濟部",
        "concurrent_limit": 5,
        "delay_range": (1.0, 2.5),
    },
    "einfo": {
        "name": "環境資訊中心",
        "concurrent_limit": 1,
        "delay_range": (8.0, 15.0),
        "blocked_limit": 50,            # einfo 頻繁 429，需要高容忍度（同 full_scan）
        "blocked_cooldown": 120.0,      # 長冷卻讓 rate limit window 重置
        "rate_limit_cooldown": 30.0,    # einfo 429 後需等 30s（預設 10s 太短）
    },
    "esg_businesstoday": {
        "name": "今周刊 ESG",
        "concurrent_limit": 3,
        "delay_range": (1.0, 2.5),
    },
    "chinatimes": {
        "name": "中國時報",
        "concurrent_limit": 3,
        "delay_range": (1.0, 2.5),
    },
}

# ==================== 輸出設定 ====================
OUTPUT_FORMAT = "tsv"
ENSURE_ASCII = True
MAX_ARTICLE_LENGTH = 20000

# ==================== 爬取模式設定 ====================
DEFAULT_MODE = "date"
DEFAULT_MAX_ARTICLES = 300
DEFAULT_DAYS_BACK = 3

# ==================== 停止條件常數（含單位註釋）====================
# --- 通用 ---
BLOCKED_CONSECUTIVE_LIMIT = 5       # 連續 N 次 403/429 回應後停止（次數）
FULL_SCAN_BLOCKED_LIMIT = 50        # Full scan 容許更多次 blocked（次數）
FULL_SCAN_BLOCKED_COOLDOWN = 120.0  # Full scan blocked 後冷卻秒數

# --- Auto Mode ---
AUTO_DEFAULT_STOP_AFTER_SKIPS = 10  # 連續 N 個已爬取文章後停止（篇數）

# --- Full Scan: Per-day adaptive suffix scanning (date-based sources) ---
# 每天掃描時，連續 N 個 404 後認定當天文章已結束，跳到隔天
DATE_SCAN_MISS_LIMIT = 80           # 連續 N 個 404 → 跳過當天剩餘 suffix
# 若接近上限時仍有文章，自動擴展 max_suffix
DATE_SCAN_AUTO_EXTEND_STEP = 200    # 每次自動擴展的 suffix 數量

# ==================== Full Scan 專用覆蓋設定 ====================
# Full scan 模式可以更積極：404 不給伺服器壓力，且有 blocked 自動停止機制
FULL_SCAN_OVERRIDES = {
    "ltn": {
        "concurrent_limit": 12,
        "delay_range": (0.1, 0.3),
        "request_timeout": 5,
        "max_candidate_urls": 0,  # LTN auto-redirects (302) to correct category; candidates waste requests
    },
    "udn": {
        "concurrent_limit": 12,
        "delay_range": (0.1, 0.3),
        "request_timeout": 5,
        "max_candidate_urls": 0,
    },
    "cna": {
        "concurrent_limit": 4,
        "delay_range": (0.5, 1.5),
        "request_timeout": 8,
        "max_candidate_urls": 0,
    },
    "einfo": {
        "concurrent_limit": 3,
        "delay_range": (1.0, 3.0),
        "request_timeout": 10,
        "max_candidate_urls": 0,
    },
    "esg_businesstoday": {
        "concurrent_limit": 6,
        "delay_range": (0.3, 0.8),
        "request_timeout": 5,
        "max_candidate_urls": 0,
    },
    "chinatimes": {
        "concurrent_limit": 5,
        "delay_range": (0.8, 2.0),
        "request_timeout": 8,
        # 每篇文章只有其正確 category code 能存取（260402 不是萬用路徑）。
        # 需嘗試多個 category — parser 提供 top 40 categories（覆蓋 95.6%）。
        # 去重用 numeric ID（engine.crawled_numeric_ids），不同 category URL 不會重爬。
        "max_candidate_urls": 39,
    },
    "moea": {
        "concurrent_limit": 2,
        "delay_range": (2.0, 4.0),
        "request_timeout": 10,
        # MOEA requires correct kind+menu_id; low concurrency to avoid 429
    },
}

# ==================== 環境感知覆蓋 ====================
# GCP e2-micro: shared vCPUs + 1GB RAM，降低併發避免 OOM
CRAWLER_ENV = os.environ.get("CRAWLER_ENV", "default")

if CRAWLER_ENV == "gcp":
    for _source, _overrides in FULL_SCAN_OVERRIDES.items():
        _overrides["concurrent_limit"] = min(_overrides.get("concurrent_limit", 3), 5)
        if _overrides.get("delay_range", (0, 0))[0] < 0.3:
            _overrides["delay_range"] = (0.3, 0.8)

# ==================== Proxy 設定 ====================
# 需要使用 proxy 的來源（IP 被封鎖時啟用）
PROXY_SOURCES = ["moea"]  # einfo IP ban 已解除 (2026-02-23)，直連更快更穩

# Proxy pool 設定
PROXY_REFRESH_INTERVAL = 600   # 10 分鐘刷新
PROXY_VALIDATE_TIMEOUT = 5     # 驗證 timeout（秒）
PROXY_MAX_POOL_SIZE = 20       # 最多保留 proxy 數量
PROXY_MAX_RETRIES = 5          # Proxy source 重試次數（免費 proxy 不穩定，多試幾個）

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

# ==================== Parser 專用設定 ====================
# UDN Parser
UDN_DEFAULT_CATEGORY = "6656"
UDN_COMMON_CATEGORIES = [
    "6656",  # 政治
    "7088",  # 生活
    "7314",  # 社會
    "6809",  # 全球
    "7238",  # 地方
    "7239",  # 兩岸
    "6885",  # 產經
]

# LTN Parser
LTN_MAIN_CATEGORIES = [
    'life', 'politics', 'society', 'world',
    'business', 'entertainment', 'sports'
]

# ESG BusinessToday Parser
ESG_BT_CATEGORIES = {
    180686: "全部",
    180687: "E永續環境",
    180688: "S社會責任",
    180689: "G公司治理",
    190807: "ESG快訊"
}

# EInfo Parser
EINFO_CATEGORY_URLS = [
    "https://e-info.org.tw/taxonomy/term/258/all",
    "https://e-info.org.tw/taxonomy/term/266",
    "https://e-info.org.tw/taxonomy/term/35283/all"
]
EINFO_DEFAULT_LATEST_ID = 242797

# 通用文本處理
MIN_PARAGRAPH_LENGTH = 20
MIN_ARTICLE_LENGTH = 50
MAX_KEYWORDS = 10

# 停用詞（用於簡易關鍵字提取）
STOPWORDS_ZH = {
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
    '都', '一', '一個', '上', '也', '很', '到', '說', '要', '去',
    '你', '會', '著', '沒有', '看', '好', '自己', '這'
}

# ==================== 覆蓋率參考點 ====================
# 用於驗證 Full Scan 是否有遺漏的已知文章參考點
# 每個參考點: {"id": article_id, "date": "YYYY-MM", "note": "描述"}
#
# Date-based sources: article_id = YYYYMMDD * multiplier + suffix
#   CNA/ESG_BT: suffix 4位 (multiplier=10000)
#   Chinatimes: suffix 6位 (multiplier=1000000)
# Sequential sources: 需手動填入或透過 auto-discover 從已爬取資料取得
REFERENCE_POINTS = {
    "cna": [
        {"id": 202401020010, "date": "2024-01", "note": "2024-01-02 第10篇"},
        {"id": 202403040010, "date": "2024-03", "note": "2024-03-04 第10篇"},
        {"id": 202405060010, "date": "2024-05", "note": "2024-05-06 第10篇"},
        {"id": 202407010010, "date": "2024-07", "note": "2024-07-01 第10篇"},
        {"id": 202409020010, "date": "2024-09", "note": "2024-09-02 第10篇"},
        {"id": 202411040010, "date": "2024-11", "note": "2024-11-04 第10篇"},
        {"id": 202501060010, "date": "2025-01", "note": "2025-01-06 第10篇"},
        {"id": 202503030010, "date": "2025-03", "note": "2025-03-03 第10篇"},
        {"id": 202505050010, "date": "2025-05", "note": "2025-05-05 第10篇"},
        {"id": 202507070010, "date": "2025-07", "note": "2025-07-07 第10篇"},
        {"id": 202509010010, "date": "2025-09", "note": "2025-09-01 第10篇"},
        {"id": 202511030010, "date": "2025-11", "note": "2025-11-03 第10篇"},
    ],
    "chinatimes": [
        {"id": 20240102000010, "date": "2024-01", "note": "2024-01-02 第10篇"},
        {"id": 20240304000010, "date": "2024-03", "note": "2024-03-04 第10篇"},
        {"id": 20240506000010, "date": "2024-05", "note": "2024-05-06 第10篇"},
        {"id": 20240701000010, "date": "2024-07", "note": "2024-07-01 第10篇"},
        {"id": 20240902000010, "date": "2024-09", "note": "2024-09-02 第10篇"},
        {"id": 20241104000010, "date": "2024-11", "note": "2024-11-04 第10篇"},
        {"id": 20250106000010, "date": "2025-01", "note": "2025-01-06 第10篇"},
        {"id": 20250303000010, "date": "2025-03", "note": "2025-03-03 第10篇"},
        {"id": 20250505000010, "date": "2025-05", "note": "2025-05-05 第10篇"},
        {"id": 20250707000010, "date": "2025-07", "note": "2025-07-07 第10篇"},
        {"id": 20250901000010, "date": "2025-09", "note": "2025-09-01 第10篇"},
        {"id": 20251103000010, "date": "2025-11", "note": "2025-11-03 第10篇"},
    ],
    "esg_businesstoday": [
        {"id": 202401020005, "date": "2024-01", "note": "2024-01-02 第5篇"},
        {"id": 202403040005, "date": "2024-03", "note": "2024-03-04 第5篇"},
        {"id": 202405060005, "date": "2024-05", "note": "2024-05-06 第5篇"},
        {"id": 202407010005, "date": "2024-07", "note": "2024-07-01 第5篇"},
        {"id": 202409020005, "date": "2024-09", "note": "2024-09-02 第5篇"},
        {"id": 202411040005, "date": "2024-11", "note": "2024-11-04 第5篇"},
        {"id": 202501060005, "date": "2025-01", "note": "2025-01-06 第5篇"},
        {"id": 202503030005, "date": "2025-03", "note": "2025-03-03 第5篇"},
    ],
    # Sequential sources: 透過 auto-discover 從已爬取資料取得
    # 或手動填入已確認存在的文章 ID
    "udn": [],
    "ltn": [],
    "einfo": [],
}
