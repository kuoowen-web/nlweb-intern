"""
text_processor.py - 文本處理工具模組

提供新聞文本處理的各種工具函數，包括智慧摘要生成、文本清理和作者名稱標準化。
移植自舊版 ltn.py 和 udn.py 的邏輯。
"""

import re
from typing import List


class TextProcessor:
    """
    文本處理工具類別
    
    提供靜態方法供 Parser 直接呼叫，無需實例化。
    所有方法均為 @staticmethod，可直接透過類別名稱呼叫。
    
    使用範例:
        from src.utils.text_processor import TextProcessor
        
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
        移植自 ltn.py 和 udn.py 的邏輯
        
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
            return summary[:20000]  # 確保不超過 20,000 字元
        
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
        參考舊版程式碼的邏輯，移除 "記者"、"報導" 等詞彙
        
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
