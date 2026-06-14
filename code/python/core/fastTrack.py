# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
This file contains the code for the 'fast track' path, which assumes that the query is a simple question,
not requiring decontextualization, query is relevant, the query has all the information needed, etc.
Those checks are done in parallel with fast track. Results are sent to the client only after
all those checks are done, which should arrive by the time the results are ready.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

from core.retriever import search
import core.ranking as ranking
from misc.logger.logging_config_helper import get_configured_logger
from core.config import CONFIG
import asyncio
from datetime import datetime, timezone, timedelta
import json

logger = get_configured_logger("fast_track")

# Sites that don't support standard vector retrieval
NO_STANDARD_RETRIEVAL_SITES = ["datacommons", "all", "conv_history", "CricketLens", "cricketlens", "cricketlens.com"]

def site_supports_standard_retrieval(site):
    """Check if a site supports standard vector database retrieval"""
    
    # If site is "all" and aggregation is disabled, treat it as supporting standard retrieval
    if site == "all" and not CONFIG.is_aggregation_enabled():
        logger.debug("Site is 'all' with aggregation disabled - treating as standard retrieval")
        return True
    
    return site not in NO_STANDARD_RETRIEVAL_SITES

class FastTrack:
    def __init__(self, handler):
        self.handler = handler
        logger.debug("FastTrack initialized")

    def is_fastTrack_eligible(self):
        """Check if query is eligible for fast track processing"""
        # Skip fast track for sites without standard retrieval
        if not site_supports_standard_retrieval(self.handler.site):
            return False
        if (self.handler.context_url != ''):
            logger.debug("Fast track not eligible: context_url present")
            return False
        if (len(self.handler.prev_queries) > 0):
            logger.debug(f"Fast track not eligible: {len(self.handler.prev_queries)} previous queries present")
            return False
        # Skip fast track for free conversation mode - no vector search needed
        if self.handler.free_conversation:
            logger.info("Fast track not eligible: free_conversation mode - skipping vector search")
            return False
        logger.info("Query is eligible for fast track")
        return True
        
    async def do(self):
        """Execute fast track processing"""
        if (not self.is_fastTrack_eligible()):
            logger.info("Fast track processing skipped - not eligible")
            return
        
        logger.info("Starting fast track processing")

        self.handler.retrieval_done_event.set()  # Use event instead of flag

        try:
            # Detect if query has temporal keywords
            temporal_keywords = ['最新', '最近', '近期', 'latest', 'recent', '新', '現在', '目前', '當前']
            is_temporal_query = any(keyword in self.handler.query for keyword in temporal_keywords)

            if is_temporal_query:
                logger.info(f"[FASTTRACK-TEMPORAL] Temporal query detected: '{self.handler.query}' - retrieving 150 items")
                num_to_retrieve = 150
            else:
                logger.info(f"[FASTTRACK-TEMPORAL] Non-temporal query - retrieving 50 items")
                num_to_retrieve = 50

            # Check if MMR is enabled and request vectors if needed
            from core.config import CONFIG
            include_vectors = CONFIG.mmr_params.get('enabled', True) and CONFIG.mmr_params.get('include_vectors', True)

            items = await search(
                self.handler.query,
                self.handler.site,
                query_params=self.handler.query_params,
                handler=self.handler,
                num_results=num_to_retrieve,
                include_vectors=include_vectors
            )

            # Pre-filter by date for temporal queries
            if is_temporal_query and len(items) > 0:
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=365)
                filtered_items = []

                for item in items:
                    # Handle both 4-tuple and 5-tuple (with vector) formats
                    if len(item) == 5:
                        url, json_str, name, site, vector = item
                    else:
                        url, json_str, name, site = item
                        vector = None
                    try:
                        schema_obj = json.loads(json_str)
                        date_published = schema_obj.get('datePublished', 'Unknown')

                        if date_published != 'Unknown':
                            # Parse date
                            date_str = date_published.split('T')[0] if 'T' in date_published else date_published
                            pub_date = datetime.strptime(date_str, '%Y-%m-%d')
                            pub_date = pub_date.replace(tzinfo=timezone.utc)

                            # Keep only recent articles
                            if pub_date >= cutoff_date:
                                if vector is not None:
                                    filtered_items.append([url, json_str, name, site, vector])
                                else:
                                    filtered_items.append([url, json_str, name, site])
                    except Exception as e:
                        # If we can't parse the date, skip this article for temporal queries
                        logger.debug(f"Could not parse date for temporal filtering: {e}")
                        pass

                # If we filtered too aggressively, take top 50 anyway
                if len(filtered_items) < 50:
                    logger.info(f"[FASTTRACK-TEMPORAL] Only {len(filtered_items)} recent articles, using all {len(items)} retrieved")
                    items = items[:80]
                else:
                    logger.info(f"[FASTTRACK-TEMPORAL] Filtered {len(items)} → {len(filtered_items)} recent articles (last 365 days)")
                    items = filtered_items[:80]

            self.handler.final_retrieved_items = items

            if (not self.handler.query_done and not self.handler.abort_fast_track_event.is_set()):
                self.handler.fastTrackRanker = ranking.Ranking(self.handler, items, ranking.Ranking.FAST_TRACK)
                await self.handler.fastTrackRanker.do()
                return
                
        except Exception as e:
            logger.error(f"Error during fast track processing: {str(e)}")
            raise