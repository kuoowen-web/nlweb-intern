"""
text_processor.py - 文本處理工具模組

提供新聞文本處理的各種工具函數，包括智慧摘要生成、文本清理和作者名稱標準化。
"""

import re
from datetime import datetime
from typing import List, Optional, Set

# 避免循環引用，延遲 import BeautifulSoup
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


class TextProcessor:
    """
    文本處理工具類別

    提供靜態方法供 Parser 直接呼叫，無需實例化。
    所有方法均為 @staticmethod，可直接透過類別名稱呼叫。

    使用範例:
        from crawler.utils.text_processor import TextProcessor

        cleaned = TextProcessor.clean_text(raw_text)
        summary = TextProcessor.smart_extract_summary(paragraphs)
        author = TextProcessor.clean_author(raw_author)
    """

    @staticmethod
    def clean_text(text: str) -> str:
        """
        清理文本，去除多餘空白、HTML tag 殘渣

        參數:
            text: 原始文本

        返回:
            清理後的文本
        """
        if not text:
            return ""

        # 移除 HTML 標籤殘渣
        text = re.sub(r'<[^>]+>', '', text)

        # 統一換行符
        text = re.sub(r'(\r\n|\r)', '\n', text)

        # 移除連續空白（但保留單一空格）
        text = re.sub(r' {2,}', ' ', text)

        # 移除行首行尾空白
        text = re.sub(r'^\s+|\s+$', '', text, flags=re.MULTILINE)

        # 移除連續多個換行（最多保留兩個換行，即一個空行）
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 移除全形空白
        text = text.replace('\u3000', ' ')

        return text.strip()

    @staticmethod
    def smart_extract_summary(paragraphs: List[str]) -> str:
        """
        智慧摘要演算法：首段 + 中間最長的兩段 + 尾段

        參數:
            paragraphs: 段落列表

        返回:
            合併後的摘要，若超過 20,000 字元則截斷
        """
        if not paragraphs:
            return ""

        # 若只有一段，直接返回
        if len(paragraphs) == 1:
            return paragraphs[0]

        # 若只有兩段，直接合併
        if len(paragraphs) == 2:
            summary = "\n\n".join(paragraphs)
            return summary[:20000]

        # 取首段
        first = paragraphs[0]

        # 取尾段
        last = paragraphs[-1]

        # 取中間段落（排除首尾段）
        middle = paragraphs[1:-1]

        # 如果沒有中間段落，只返回首尾段
        if not middle:
            summary = f"{first}\n\n{last}"
            return summary[:20000]

        # 按長度排序中間段落，取最長的兩段
        sorted_middle = sorted(middle, key=len, reverse=True)
        selected = sorted_middle[:min(2, len(sorted_middle))]

        # 找出選中的中段在原文中的位置，並按原順序添加
        selected_ordered = [p for p in middle if p in selected]

        # 合併成最終摘要：首段 + 選中的中段（保持原順序）+ 尾段
        summary = "\n\n".join([first] + selected_ordered + [last])

        # 確保不超過 20,000 字元
        if len(summary) > 20000:
            summary = summary[:20000]

        return summary

    @staticmethod
    def clean_author(raw_author: str) -> str:
        """
        清理作者名稱，移除常見贅字

        參數:
            raw_author: 原始作者字串

        返回:
            清理後的作者名稱
        """
        if not raw_author:
            return ""

        # 移除常見贅字
        redundant_terms = [
            "記者", "編輯", "報導", "採訪", "撰文",
            "文／", "／", "特派員", "綜合報導",
            "整理", "責任編輯", "中心", "特約",
            "資深", "駐", "報道", "電", "攝影",
            "圖", "文", "編譯"
        ]

        cleaned_author = raw_author

        # 逐一移除贅字
        for term in redundant_terms:
            cleaned_author = cleaned_author.replace(term, "")

        # 移除括號內容，如 (地區) 或 （地區）
        cleaned_author = re.sub(r'[\(（].*?[\)）]', '', cleaned_author)

        # 移除斜線及其後的內容（如：張三/台北）
        cleaned_author = re.sub(r'[/／].*$', '', cleaned_author)

        # 移除多餘空格
        cleaned_author = re.sub(r'\s+', ' ', cleaned_author).strip()

        # 移除前後的標點符號
        cleaned_author = cleaned_author.strip('、，。：；！？')

        return cleaned_author

    # ==================== 共用關鍵字提取方法 ====================

    @staticmethod
    def extract_keywords_from_soup(
        soup,
        title: str = "",
        max_keywords: int = 10
    ) -> List[str]:
        """
        從 HTML soup 提取關鍵字（多種策略）

        策略順序：
        1. meta name="keywords"
        2. meta property="article:tag"
        3. meta name="news_keywords"
        4. 備用：從標題簡易提取

        參數:
            soup: BeautifulSoup 物件
            title: 文章標題（用於備用提取）
            max_keywords: 最大關鍵字數量

        返回:
            關鍵字列表
        """
        keywords = []

        # 方法 1：從 meta name="keywords" 提取
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords and meta_keywords.get('content'):
            content = meta_keywords['content']
            keywords = [
                kw.strip()
                for kw in re.split(r'[,，、;；]', content)
                if kw.strip()
            ]

        # 方法 2：從 meta property="article:tag" 提取
        if not keywords:
            article_tags = soup.find_all('meta', property='article:tag')
            keywords = [
                tag['content'].strip()
                for tag in article_tags
                if tag.get('content')
            ]

        # 方法 3：從 meta name="news_keywords" 提取
        if not keywords:
            news_keywords = soup.find('meta', attrs={'name': 'news_keywords'})
            if news_keywords and news_keywords.get('content'):
                content = news_keywords['content']
                keywords = [
                    kw.strip()
                    for kw in re.split(r'[,，、;；]', content)
                    if kw.strip()
                ]

        # 方法 4：備用 - 從標題簡易提取
        if not keywords and title:
            keywords = TextProcessor.simple_keyword_extraction(title)

        return keywords[:max_keywords]

    @staticmethod
    def simple_keyword_extraction(
        title: str,
        stopwords: Optional[Set[str]] = None,
        max_keywords: int = 5
    ) -> List[str]:
        """
        從標題簡易提取關鍵字

        參數:
            title: 標題文字
            stopwords: 停用詞集合（可選，預設使用中文停用詞）
            max_keywords: 最大關鍵字數量

        返回:
            關鍵字列表
        """
        # 預設中文停用詞
        if stopwords is None:
            stopwords = {
                '的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
                '都', '一', '一個', '上', '也', '很', '到', '說', '要', '去',
                '你', '會', '著', '沒有', '看', '好', '自己', '這'
            }

        # 移除標點符號
        title_clean = re.sub(r'[^\w\s]', ' ', title)

        # 分詞
        words = title_clean.split()

        # 過濾：2-4 字且不在停用詞中
        keywords = [
            word for word in words
            if 2 <= len(word) <= 4 and word not in stopwords
        ]

        return keywords[:max_keywords]

    # ==================== 共用日期解析方法 ====================

    @staticmethod
    def parse_iso_date(date_str: str) -> Optional[datetime]:
        """
        解析 ISO 日期字串，自動處理時區後綴

        參數:
            date_str: ISO 格式日期字串（如 "2025-01-28T10:30:00+08:00"）

        返回:
            datetime 物件，或 None（解析失敗）
        """
        if not date_str:
            return None

        try:
            # 移除時區後綴
            date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
            return datetime.fromisoformat(date_str)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_date_string(time_str: str) -> Optional[datetime]:
        """
        解析常見日期格式字串

        支援格式：
        - YYYY-MM-DD HH:MM
        - YYYY/MM/DD HH:MM

        參數:
            time_str: 日期時間字串

        返回:
            datetime 物件，或 None（解析失敗）
        """
        if not time_str:
            return None

        try:
            if '-' in time_str and ':' in time_str:
                return datetime.strptime(time_str, '%Y-%m-%d %H:%M')
            if '/' in time_str and ':' in time_str:
                return datetime.strptime(time_str, '%Y/%m/%d %H:%M')
        except (ValueError, TypeError):
            pass

        return None

    # ==================== 共用段落處理方法 ====================

    @staticmethod
    def filter_paragraph(
        text: str,
        min_length: int = 20,
        blacklist_terms: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        過濾並清理段落文字

        參數:
            text: 原始段落文字
            min_length: 最小長度門檻
            blacklist_terms: 黑名單詞彙列表

        返回:
            清理後的段落，或 None（不符合條件）
        """
        if not text:
            return None

        text = text.strip()

        # 長度檢查
        if len(text) < min_length:
            return None

        # 預設黑名單
        if blacklist_terms is None:
            blacklist_terms = [
                '訂閱', '廣告', '相關新聞', '延伸閱讀',
                '推薦閱讀', '更多新聞', '請繼續往下閱讀'
            ]

        # 黑名單檢查
        for term in blacklist_terms:
            if term in text:
                return None

        # 清理文字
        cleaned = TextProcessor.clean_text(text)

        return cleaned if cleaned else None

    @staticmethod
    def remove_noise_elements(soup, selectors: Optional[List[str]] = None) -> None:
        """
        從 soup 中移除雜訊元素（原地修改）

        參數:
            soup: BeautifulSoup 物件或其子元素
            selectors: CSS 選擇器列表

        注意:
            此方法會原地修改 soup 物件
        """
        if selectors is None:
            selectors = [
                'script', 'style', 'iframe', 'aside',
                '.ad', '.advertisement', '.ads',
                '.related-news', '.recommend', '.related',
                '.social-share', '.share-buttons',
            ]

        for selector in selectors:
            for element in soup.select(selector):
                element.decompose()
