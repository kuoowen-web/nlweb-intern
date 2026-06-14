"""
html_analyzer.py - HTML 特徵分析模組

提供 HTML 結構特徵分析功能，用於新聞內容分級。
這是 NLWeb v2 的核心功能，舊版程式碼中沒有此部分。
"""

from bs4 import BeautifulSoup
from typing import Dict, Any, List, Optional


class HTMLAnalyzer:
    """HTML 特徵分析器類別"""
    
    def __init__(self, soup: BeautifulSoup):
        """
        初始化分析器
        
        參數:
            soup: BeautifulSoup 物件
        """
        self.soup = soup
        self.content_area = self._identify_content_area()
    
    def _identify_content_area(self) -> BeautifulSoup:
        """
        識別文章主要內容區域
        嘗試排除導航列、側邊欄等非內容區域
        
        返回:
            內容區域的 BeautifulSoup 物件，若無法識別則返回原始 soup
        """
        # 常見的內容區域選擇器（優先順序由高到低）
        content_selectors = [
            'article',
            '.article',
            '#article',
            '.article-content',
            '.article-body',
            '.content',
            '#content',
            '.story',
            '.news-content',
            '.whitecon',  # 自由時報特有
            '.story-list__text',  # 聯合報特有
            '.article-text',
            'main',
            '.main-content'
        ]
        
        for selector in content_selectors:
            content = self.soup.select_one(selector)
            if content:
                return content
        
        # 若無法識別，返回原始 soup
        return self.soup
    
    def extract_features(self) -> Dict[str, Any]:
        """
        提取 HTML 特徵
        
        返回:
            包含各種特徵的字典
        """
        features = {
            # 連結特徵
            "link_count": self._count_links(),
            
            # 表格特徵
            "has_table": self._has_table(),
            
            # 圖片特徵
            "image_count": self._count_images(),
            
            # 標題階層特徵
            "h2_count": self._count_tags("h2"),
            "h3_count": self._count_tags("h3"),
            
            # Meta 資訊
            "meta_tags": self._extract_meta_tags()
        }
        
        # 附加其他結構特徵
        features.update(self._extract_additional_features())
        
        return features
    
    def _count_links(self) -> int:
        """
        計算內容區域中的連結數量
        盡量排除導航列的連結，只計算內文區塊的連結
        """
        # 找出所有需要排除的區域
        excluded_areas = self.soup.find_all(['nav', 'header', 'footer', 'aside', 'menu'])
        excluded_links = set()
        
        for area in excluded_areas:
            for link in area.find_all('a'):
                excluded_links.add(link)
        
        # 計算內容區域的連結
        all_content_links = self.content_area.find_all('a')
        
        # 排除導航連結
        content_links = [link for link in all_content_links if link not in excluded_links]
        
        return len(content_links)
    
    def _has_table(self) -> bool:
        """檢查是否包含表格"""
        return len(self.content_area.find_all('table')) > 0
    
    def _count_images(self) -> int:
        """計算內容區域中的圖片數量"""
        return len(self.content_area.find_all('img'))
    
    def _count_tags(self, tag_name: str) -> int:
        """計算特定標籤的數量"""
        return len(self.content_area.find_all(tag_name))
    
    def _extract_meta_tags(self) -> Dict[str, str]:
        """
        提取 meta 標籤資訊
        嘗試抓取 keywords, description, og:type 等
        """
        meta_info = {}
        
        # 提取 keywords
        keywords_meta = self.soup.find('meta', attrs={'name': 'keywords'})
        if keywords_meta and keywords_meta.get('content'):
            meta_info['keywords'] = keywords_meta['content']
        
        # 提取 description
        description_meta = self.soup.find('meta', attrs={'name': 'description'})
        if description_meta and description_meta.get('content'):
            meta_info['description'] = description_meta['content']
        
        # 提取 og:type
        og_type_meta = self.soup.find('meta', attrs={'property': 'og:type'})
        if og_type_meta and og_type_meta.get('content'):
            meta_info['og_type'] = og_type_meta['content']
        
        # 提取 og:title
        og_title_meta = self.soup.find('meta', attrs={'property': 'og:title'})
        if og_title_meta and og_title_meta.get('content'):
            meta_info['og_title'] = og_title_meta['content']
        
        # 提取 article:published_time
        published_time_meta = self.soup.find('meta', attrs={'property': 'article:published_time'})
        if published_time_meta and published_time_meta.get('content'):
            meta_info['published_time'] = published_time_meta['content']
        
        # 提取 article:author
        author_meta = self.soup.find('meta', attrs={'property': 'article:author'})
        if author_meta and author_meta.get('content'):
            meta_info['article_author'] = author_meta['content']
        
        # 提取 article:section
        section_meta = self.soup.find('meta', attrs={'property': 'article:section'})
        if section_meta and section_meta.get('content'):
            meta_info['article_section'] = section_meta['content']
        
        return meta_info
    
    def _extract_additional_features(self) -> Dict[str, Any]:
        """
        提取額外的結構特徵
        用於更精確的內容分級
        """
        additional_features = {
            # 段落數量
            "paragraph_count": self._count_tags("p"),
            
            # 強調文本數量（粗體、斜體等）
            "emphasis_count": len(self.content_area.find_all(['strong', 'em', 'b', 'i'])),
            
            # 引用區塊數量
            "blockquote_count": self._count_tags("blockquote"),
            
            # 列表數量（有序和無序）
            "list_count": len(self.content_area.find_all(['ul', 'ol'])),
            
            # 列表項目數量
            "list_item_count": self._count_tags("li"),
            
            # iframe 數量（通常用於嵌入影片或社交媒體）
            "iframe_count": self._count_tags("iframe"),
            
            # 是否包含影片
            "has_video": self._has_video(),
            
            # h1 標題數量
            "h1_count": self._count_tags("h1"),
            
            # h4 標題數量
            "h4_count": self._count_tags("h4"),
            
            # div 數量（結構複雜度指標）
            "div_count": self._count_tags("div"),
            
            # span 數量
            "span_count": self._count_tags("span")
        }
        
        return additional_features
    
    def _has_video(self) -> bool:
        """檢查是否包含影片"""
        # 檢查 video 標籤
        if self.content_area.find('video'):
            return True
        
        # 檢查 YouTube 或 Vimeo 的 iframe
        iframes = self.content_area.find_all('iframe')
        for iframe in iframes:
            src = iframe.get('src', '')
            if 'youtube' in src.lower() or 'vimeo' in src.lower():
                return True
        
        return False


# 提供便捷函式，方便其他模組使用
def extract_features(soup: BeautifulSoup) -> Dict[str, Any]:
    """
    從 BeautifulSoup 物件中提取 HTML 特徵
    
    參數:
        soup: BeautifulSoup 物件
        
    返回:
        包含各種特徵的字典
    """
    analyzer = HTMLAnalyzer(soup)
    return analyzer.extract_features()
