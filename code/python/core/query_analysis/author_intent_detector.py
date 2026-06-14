"""
Author Intent Detector - Identifies author/reporter search intent in queries.

Detects when a user is searching for articles by a specific author or reporter,
and extracts the author name for use as a retriever-level payload filter.

Examples:
- "林彥良的文章" → {is_author_search: True, author_name: '林彥良'}
- "記者王小明" → {is_author_search: True, author_name: '王小明'}
- "articles by John Smith" → {is_author_search: True, author_name: 'John Smith'}
- "AI趨勢分析" → {is_author_search: False}
"""

import re
from typing import Dict, Optional
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("author_intent_detector")


class AuthorIntentDetector:
    """
    Detects author search intent and extracts author names from queries.
    Integrates with the handler's pre-check pattern.
    """

    STEP_NAME = "AuthorIntentDetector"

    # Regex patterns for author search intent (bilingual)
    AUTHOR_PATTERNS = {
        # Chinese patterns
        'author_articles_zh': r'(?:記者|作者|編輯)\s*([^\s的寫]{2,8})\s*(?:的文章|的報導|寫的|的新聞)',
        'reporter_name_zh': r'(?:記者|作者)\s*([^\s]{2,8})',
        'by_author_zh': r'([^\s]{2,8})\s*(?:記者|作者|編輯)',

        # English patterns
        'articles_by_en': r'(?:articles?|posts?|stories?|reports?)\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        'by_author_en': r'by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        'reporter_en': r'(?:reporter|journalist|author|writer)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
    }

    # Stopwords that should NOT be matched as author names
    STOPWORDS_ZH = {
        '什麼', '怎麼', '如何', '為什麼', '哪些', '這個', '那個', '最新', '最近',
        '今天', '昨天', '本週', '分析', '報導', '新聞', '文章', '發展', '趨勢',
        '技術', '產業', '市場', '公司', '企業', '科技', '經濟', '金融', '政治',
    }

    def __init__(self, handler):
        self.handler = handler
        self.handler.state.start_precheck_step(self.STEP_NAME)

    async def do(self):
        """Run author intent detection and store result on handler."""
        # Wait for decontextualization to finish before accessing the query
        await self.handler.state._decon_event.wait()
        query = self.handler.decontextualized_query or self.handler.query
        result = self._detect_author_intent(query)
        self.handler.author_search = result

        if result and result.get('is_author_search'):
            logger.info(f"[AUTHOR] Detected author search: '{result['author_name']}' from query: '{query}'")
        else:
            logger.debug(f"[AUTHOR] No author intent detected in query: '{query}'")

        await self.handler.state.precheck_step_done(self.STEP_NAME)
        return result

    def _detect_author_intent(self, query: str) -> Dict:
        """
        Detect author search intent using regex patterns.

        Returns:
            Dict with keys: is_author_search, author_name, pattern_matched
        """
        if not query:
            return {'is_author_search': False}

        for pattern_name, pattern in self.AUTHOR_PATTERNS.items():
            match = re.search(pattern, query)
            if match:
                author_name = match.group(1).strip()

                # Validate: reject stopwords and too-short names
                if author_name in self.STOPWORDS_ZH:
                    continue
                if len(author_name) < 2:
                    continue

                return {
                    'is_author_search': True,
                    'author_name': author_name,
                    'pattern_matched': pattern_name
                }

        return {'is_author_search': False}
