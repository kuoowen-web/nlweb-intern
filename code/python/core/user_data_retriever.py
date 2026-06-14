# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Helper module for retrieving user-uploaded private files during search.

This module integrates private file retrieval with the existing search pipeline.
"""

from typing import List, Dict, Any, Optional
from retrieval_providers.user_postgres_provider import get_user_postgres_provider
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_data_retriever")


async def search_user_documents(
    query: str,
    user_id: str,
    top_k: int = 10,
    query_params: Optional[Dict] = None,
    org_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search user's private documents.

    Args:
        query: Search query
        user_id: User identifier
        top_k: Number of results to return
        query_params: Optional query parameters
        org_id: Organization identifier for org-level isolation (optional)

    Returns:
        List of search results from user's private files
    """
    if not user_id:
        logger.warning("No user_id provided for private document search")
        return []

    try:
        provider = get_user_postgres_provider()
        results = await provider.search_user_documents(
            query=query,
            user_id=user_id,
            top_k=top_k,
            query_params=query_params,
            org_id=org_id,
        )

        logger.info(f"Retrieved {len(results)} results from user's private documents")
        return results

    except Exception as e:
        logger.exception(f"Error searching user documents: {str(e)}")
        return []


async def merge_public_and_private_results(
    public_results: List[Dict[str, Any]],
    private_results: List[Dict[str, Any]],
    private_first: bool = True
) -> List[Dict[str, Any]]:
    """
    Merge public and private search results.

    Args:
        public_results: Results from public data sources
        private_results: Results from user's private files
        private_first: If True, private results come first

    Returns:
        Merged list of results
    """
    if private_first:
        # Private results first (higher priority)
        merged = private_results + public_results
    else:
        # Mix them (could implement more sophisticated merging strategies)
        merged = public_results + private_results

    logger.info(f"Merged results: {len(private_results)} private + {len(public_results)} public = {len(merged)} total")
    return merged


def format_private_result_for_display(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a private file search result to match the expected display format.

    Args:
        result: Raw result from UserQdrantProvider

    Returns:
        Formatted result dictionary
    """
    # The result from UserQdrantProvider already has most fields
    # This function can be used to add any additional formatting needed for display

    formatted = {
        'url': result.get('url', ''),
        'title': f"📄 私人文件 (片段 {result.get('chunk_index', 0) + 1}/{result.get('total_chunks', 1)})",
        'text': result.get('content', ''),
        'site': '我的知識庫',
        'score': result.get('score', 0.0),
        'source_type': 'private',
        'metadata': result.get('metadata', {})
    }

    return formatted
