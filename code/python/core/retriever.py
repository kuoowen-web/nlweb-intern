# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Unified vector database interface with support for Azure AI Search, Milvus, and Qdrant.
This module provides abstract base classes and concrete implementations for database operations.
"""

import os
import time
import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, Tuple, Type
import json

from core.config import CONFIG
from core.utils.utils import get_param
from core.utils.json_utils import jsonify as _jsonify
from misc.logger.logging_config_helper import get_configured_logger
from misc.logger.logger import LogLevel
from core.embedding import batch_get_embeddings


def _merge_json_objects(obj1, obj2):
    """Recursively merge two JSON objects."""
    obj1 = _jsonify(obj1)
    obj2 = _jsonify(obj2)
    if isinstance(obj1, list):
        obj1 = obj1[0] if obj1 else {}
    if isinstance(obj2, list):
        obj2 = obj2[0] if obj2 else {}
    if not isinstance(obj1, dict):
        obj1 = {}
    if not isinstance(obj2, dict):
        obj2 = {}
    merged = {}
    for key in set(obj1.keys()) | set(obj2.keys()):
        val1 = obj1.get(key)
        val2 = obj2.get(key)
        if key in obj1 and key not in obj2:
            merged[key] = val1
        elif key not in obj1 and key in obj2:
            merged[key] = val2
        elif isinstance(val1, dict) and isinstance(val2, dict):
            merged[key] = _merge_json_objects(val1, val2)
        elif isinstance(val1, list) and isinstance(val2, list):
            merged[key] = val1 + val2
        elif val1 is None:
            merged[key] = val2
        elif val2 is None:
            merged[key] = val1
        elif val1 == val2:
            merged[key] = val1
        else:
            merged[key] = [val1, val2]
    return merged


def merge_json_array(json_array):
    """Merge an array of JSON objects into a single object."""
    if not json_array:
        return {}
    result = _jsonify(json_array[0])
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        result = {}
    for obj in json_array[1:]:
        result = _merge_json_objects(result, obj)
    return result

logger = get_configured_logger("retriever")

# Client caches for reusing instances
_low_level_client_cache = {}   # For raw DB clients (Qdrant, etc.)
_vector_client_cache = {}       # For VectorDBClient instances
_client_cache_lock = asyncio.Lock()

# Preloaded client modules
_preloaded_modules = {}

def init():
    """Initialize retrieval clients based on configuration."""
    # Preload modules for enabled endpoints
    for endpoint_name, endpoint_config in CONFIG.retrieval_endpoints.items():
        if endpoint_config.enabled and endpoint_config.db_type:
            db_type = endpoint_config.db_type
            try:
                # Ensure packages are installed
                _ensure_package_installed(db_type)
                
                # Preload the module
                if db_type == "qdrant":
                    from retrieval_providers.qdrant import QdrantVectorClient
                    _preloaded_modules[db_type] = QdrantVectorClient
                elif db_type == "postgres":
                    from retrieval_providers.postgres_client import PgVectorClient
                    _preloaded_modules[db_type] = PgVectorClient
                else:
                    logger.warning(f"Unsupported db_type: {db_type} (only qdrant and postgres are supported)")

            except Exception as e:
                logger.warning(f"Failed to preload {db_type} client module: {e}")

# Mapping of database types to their required pip packages
_db_type_packages = {
    "qdrant": ["qdrant-client>=1.14.0"],
    "postgres": ["psycopg", "psycopg[binary]>=3.1.12", "psycopg[pool]>=3.2.0", "pgvector>=0.4.0"],
}

# Cache for installed packages
_installed_packages = set()

def _ensure_package_installed(db_type: str):
    """
    Ensure the required packages for a database type are installed.
    
    Args:
        db_type: The type of database backend
    """
    if db_type not in _db_type_packages:
        return
    
    packages = _db_type_packages[db_type]
    for package in packages:
        # Extract package name without version for caching
        package_name = package.split(">=")[0].split("==")[0].split("[")[0]
        
        if package_name in _installed_packages:
            continue
            
        try:
            # Try to import the package first
            if package_name == "azure-core":
                __import__("azure.core")
            elif package_name == "azure-search-documents":
                __import__("azure.search.documents")
            elif package_name == "qdrant-client":
                __import__("qdrant_client")
            elif package_name == "elasticsearch":
                __import__("elasticsearch")
            elif package_name == "psycopg":
                __import__("psycopg")
            elif package_name == "hnswlib":
                __import__("hnswlib")
            else:
                __import__(package_name)
            _installed_packages.add(package_name)
            logger.debug(f"Package {package_name} is already installed")
        except ImportError:
            raise ImportError(
                f"Package '{package_name}' is required for {db_type} backend. "
                f"Install with: pip install {package}"
            )


class VectorDBClientInterface(ABC):
    """
    Abstract base class defining the interface for vector database clients.
    All vector database implementations should implement these methods.
    """
    
    @abstractmethod
    async def delete_documents_by_site(self, site: str, **kwargs) -> int:
        """
        Delete all documents matching the specified site.
        
        Args:
            site: Site identifier
            **kwargs: Additional parameters
            
        Returns:
            Number of documents deleted
        """
        pass
    
    @abstractmethod
    async def upload_documents(self, documents: List[Dict[str, Any]], **kwargs) -> int:
        """
        Upload documents to the database.
        
        Args:
            documents: List of document objects
            **kwargs: Additional parameters
            
        Returns:
            Number of documents uploaded
        """
        pass
    
    @abstractmethod
    async def search(self, query: str, site: Union[str, List[str]], 
                    num_results: int = 50, **kwargs) -> List[List[str]]:
        """
        Search for documents matching the query and site.
        
        Args:
            query: Search query string
            site: Site identifier or list of sites
            num_results: Maximum number of results to return
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        pass
    
    @abstractmethod
    async def search_by_url(self, url: str, **kwargs) -> Optional[List[str]]:
        """
        Retrieve a document by its exact URL.
        
        Args:
            url: URL to search for
            **kwargs: Additional parameters
            
        Returns:
            Document data or None if not found
        """
        pass
    
    @abstractmethod
    async def search_all_sites(self, query: str, num_results: int = 50, **kwargs) -> List[List[str]]:
        """
        Search across all sites.
        
        Args:
            query: Search query string
            num_results: Maximum number of results to return
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        pass
    
    async def get_sites(self, **kwargs) -> Optional[List[str]]:
        """
        Get list of all sites available in the database.
        
        Args:
            **kwargs: Additional parameters
            
        Returns:
            List of site names if supported, None if not supported by this backend.
            
        Note:
            Backends that don't support this method should return None.
            The default implementation returns None.
        """
       
        return None


class RetrievalClientBase(VectorDBClientInterface):
    """
    Base implementation for retrieval clients with default caching behavior.
    All retrieval provider implementations should inherit from this class.
    """
    
    def __init__(self):
        """Initialize the base client with caching structures."""
        # Cache for available sites
        self._sites_cache: Optional[List[str]] = None
        self._sites_cache_time: float = 0
        self._cache_expiry_seconds = 300  # 5 minutes cache expiry
        self._cache_lock = asyncio.Lock()
    
    async def can_handle_query(self, site: Union[str, List[str]], **kwargs) -> bool:
        """
        Check if this provider can handle a query for the given site(s).
        Implements caching with stale-while-revalidate pattern.
        
        Args:
            site: Site identifier or list of sites
            **kwargs: Additional parameters
            
        Returns:
            True if the provider can handle queries for at least one of the requested sites
        """
        # Handle 'all' case - always return True
        if site == "all":
            return True
        
        # Get cached or fresh sites list
        available_sites = await self._get_cached_sites()
        
        # If get_sites is not supported or errored, assume provider might have the site
        if available_sites is None:
            return True
        
        # If no sites available, provider can't handle any query
        if not available_sites:
            return False
        
        # Convert site to list for uniform handling
        sites_to_check = [site] if isinstance(site, str) else site
        
        # Check if any requested site is available
        return any(s in available_sites for s in sites_to_check)
    
    async def _get_cached_sites(self) -> Optional[List[str]]:
        """
        Get sites list with caching and background refresh.
        Uses stale-while-revalidate pattern for better performance.
        
        Returns:
            List of available sites or None if get_sites is not supported
        """
        current_time = time.time()
        cache_age = current_time - self._sites_cache_time
        
        # If we have cache and it's fresh, return it immediately
        if self._sites_cache is not None and cache_age < self._cache_expiry_seconds:
            return self._sites_cache
        
        # If we have stale cache (but not too old), return it and refresh in background
        if self._sites_cache is not None and cache_age < self._cache_expiry_seconds * 10:
            logger.debug(f"Returning stale sites cache (age: {cache_age:.1f}s), refreshing in background")
            # Start background refresh (fire and forget)
            asyncio.create_task(self._refresh_sites_cache())
            return self._sites_cache
        
        # No cache or very old cache - fetch synchronously
        async with self._cache_lock:
            # Check again in case another coroutine just updated it
            if self._sites_cache is not None:
                cache_age = time.time() - self._sites_cache_time
                if cache_age < self._cache_expiry_seconds:
                    return self._sites_cache
            
            try:
                sites = await self.get_sites()
                self._sites_cache = sites
                self._sites_cache_time = current_time
                if sites:
                    logger.info(f"Provider has {len(sites)} sites: {sites[:5]}{'...' if len(sites) > 5 else ''}")
                return sites
            except AttributeError:
                # get_sites method doesn't exist - not supported by this backend
                logger.debug("Provider does not support get_sites()")
                self._sites_cache = None
                self._sites_cache_time = current_time
                return None
            except Exception as e:
                logger.warning(f"Failed to get sites from provider: {e}")
                # Keep using old cache if available
                return self._sites_cache
    
    async def _refresh_sites_cache(self) -> None:
        """Refresh the sites cache in the background."""
        try:
            sites = await self.get_sites()
            async with self._cache_lock:
                self._sites_cache = sites
                self._sites_cache_time = time.time()
            if sites:
                logger.debug(f"Background refresh: Provider has {len(sites)} sites")
        except Exception as e:
            logger.warning(f"Background refresh of sites cache failed: {e}")
            # Don't update cache - keep using stale value


class VectorDBClient:
    """
    Unified client for vector database operations. This class routes operations to the appropriate
    client implementation based on the database type specified in configuration.
    """
    
    def __init__(self, endpoint_name: Optional[str] = None, query_params: Optional[Dict[str, Any]] = None):
        """
        Initialize the database client.
        
        Args:
            endpoint_name: Optional name of the endpoint to use (for backward compatibility)
            query_params: Optional query parameters for overriding endpoint
        """
        self.query_params = query_params or {}
        self.endpoint_name = endpoint_name  # Store the endpoint name
        self.db_type = None  # Will be set based on the primary endpoint
        
        # In development mode, check if query_params specifies a database endpoint
        if CONFIG.is_development_mode() and self.query_params:
            # Check for 'db' or 'retrieval_backend' parameter
            param_endpoint = self.query_params.get('db') or self.query_params.get('retrieval_backend')
            if param_endpoint:
                # Handle case where param_endpoint might be a list
                if isinstance(param_endpoint, list):
                    if len(param_endpoint) > 0:
                        param_endpoint = param_endpoint[0]
                        logger.warning(f"Development mode: 'db' parameter was a list, using first element: {param_endpoint}")
                    else:
                        logger.error("Development mode: 'db' parameter is an empty list")
                        param_endpoint = None
                
                if param_endpoint:
                    logger.info(f"Development mode: Using database endpoint from params: {param_endpoint}")
                    endpoint_name = param_endpoint
        
        # If specific endpoint requested, validate and use it
        if endpoint_name:
            try:
                if endpoint_name not in CONFIG.retrieval_endpoints:
                    available_endpoints = list(CONFIG.retrieval_endpoints.keys())
                    error_msg = f"Invalid endpoint: '{endpoint_name}'. Available endpoints: {', '.join(available_endpoints)}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
            except TypeError as e:
                # This can happen if endpoint_name is unhashable (e.g., a list)
                error_msg = f"Invalid endpoint name type: {type(endpoint_name).__name__}. Expected string, got: {endpoint_name}"
                logger.error(error_msg)
                raise ValueError(error_msg) from e
            
            # For backward compatibility, use only the specified endpoint
            endpoint_config = CONFIG.retrieval_endpoints[endpoint_name]
            self.enabled_endpoints = {endpoint_name: endpoint_config}
            self.db_type = endpoint_config.db_type  # Set db_type from the endpoint
            logger.info(f"VectorDBClient initialized with specific endpoint: {endpoint_name}")
        else:
            # Get all enabled endpoints and validate they have required credentials
            self.enabled_endpoints = {}
            for name, config in CONFIG.retrieval_endpoints.items():
                if not config.enabled:
                    continue
                    
                # Check if endpoint has required credentials based on db_type
                if self._has_valid_credentials(name, config):
                    self.enabled_endpoints[name] = config
                else:
                    logger.warning(f"Endpoint {name} is enabled but missing required credentials, skipping")
            
            if not self.enabled_endpoints:
                error_msg = "No enabled retrieval endpoints with valid credentials found"
                logger.error(error_msg)
                # Debug: show which endpoints were checked and why they were skipped
                for name, config in CONFIG.retrieval_endpoints.items():
                    if config.enabled:
                        logger.error(f"Endpoint {name} was enabled but skipped - missing credentials?")
                raise ValueError(error_msg)
            
            # Set db_type to the first enabled endpoint's type (for logging)
            if self.enabled_endpoints:
                first_endpoint = next(iter(self.enabled_endpoints.values()))
                self.db_type = first_endpoint.db_type
            
            logger.info(f"VectorDBClient initialized with {len(self.enabled_endpoints)} enabled endpoints: {list(self.enabled_endpoints.keys())}")
        
        # Validate write endpoint if configured (skip for specific endpoint mode)
        self.write_endpoint = CONFIG.write_endpoint
        if self.write_endpoint and not endpoint_name:  # Only validate write endpoint if not in specific endpoint mode
            if self.write_endpoint not in CONFIG.retrieval_endpoints:
                raise ValueError(f"Write endpoint '{self.write_endpoint}' not found in configuration")
            
            write_config = CONFIG.retrieval_endpoints[self.write_endpoint]
            if not self._has_valid_credentials(self.write_endpoint, write_config):
                raise ValueError(f"Write endpoint '{self.write_endpoint}' is missing required credentials")
            
            logger.info(f"Write operations will use endpoint: {self.write_endpoint}")
        elif not endpoint_name:
            logger.warning("No write endpoint configured - write operations will fail")
        
        self._retrieval_lock = asyncio.Lock()

    def _has_valid_credentials(self, name: str, config) -> bool:
        """
        Check if an endpoint has valid credentials based on its database type.
        
        Args:
            name: Endpoint name
            config: Endpoint configuration
            
        Returns:
            True if endpoint has required credentials
        """
        db_type = config.db_type
        
        if db_type in ["azure_ai_search", "snowflake_cortex_search", "opensearch", "milvus"]:
            # These require API key and endpoint
            logger.debug(f"Checking credentials for {name} (type: {db_type})")
            logger.debug(f"  api_key: {bool(config.api_key)} ({config.api_key[:10] + '...' if config.api_key else 'None'})")
            logger.debug(f"  api_endpoint: {bool(config.api_endpoint)} ({config.api_endpoint if config.api_endpoint else 'None'})")
            return bool(config.api_key and config.api_endpoint)
        elif db_type == "qdrant":
            # Qdrant can use either local path or remote URL
            if config.database_path:
                return True  # Local file-based storage
            else:
                return bool(config.api_endpoint)  # Remote server (api_key is optional)
        elif db_type == "elasticsearch":
            # Elasticsearch requires endpoint, API key is optional
            return bool(config.api_endpoint)
        elif db_type == "postgres":
            # PostgreSQL requires endpoint (connection string) and optionally api_key (password)
            return bool(config.api_endpoint)
        elif db_type == "shopify_mcp":
            # Shopify MCP doesn't require authentication
            return True
        elif db_type == "cloudflare_autorag":
            return bool(config.api_key)
        elif db_type == "hnswlib":
            # HNSW requires a database path to the pre-built index
            return bool(config.database_path)
        else:
            logger.warning(f"Unknown database type {db_type} for endpoint {name}")
            return False
    
    async def get_client(self, endpoint_name: str) -> VectorDBClientInterface:
        """
        Get or initialize the appropriate vector database client for a specific endpoint.
        Uses a cache to avoid creating duplicate client instances.
        
        Args:
            endpoint_name: Name of the endpoint to get client for
            
        Returns:
            Appropriate vector database client
        """
        if endpoint_name not in self.enabled_endpoints:
            raise ValueError(f"Endpoint {endpoint_name} is not in enabled endpoints")
            
        config = self.enabled_endpoints[endpoint_name]
        db_type = config.db_type
        
        # Use cache key combining db_type and endpoint
        cache_key = f"{db_type}_{endpoint_name}"
        
        # Check if client already exists in cache
        async with _client_cache_lock:
            if cache_key in _low_level_client_cache:
                return _low_level_client_cache[cache_key]
            
            # Ensure required packages are installed
            _ensure_package_installed(db_type)
            
            # Create the appropriate client with dynamic imports
            logger.debug(f"Creating new client for {db_type} with endpoint {endpoint_name}")
            
            try:
                # Use preloaded module if available, otherwise load on demand
                if db_type in _preloaded_modules:
                    client_class = _preloaded_modules[db_type]
                    client = client_class(endpoint_name)
                elif db_type == "qdrant":
                    from retrieval_providers.qdrant import QdrantVectorClient
                    client = QdrantVectorClient(endpoint_name)
                elif db_type == "postgres":
                    from retrieval_providers.postgres_client import PgVectorClient
                    client = PgVectorClient(endpoint_name)
                else:
                    error_msg = f"Unsupported database type: {db_type}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
            except ImportError as e:
                logger.error(f"Failed to import client for {db_type}: {e}")
                raise ValueError(f"Failed to load client for {db_type}: {e}")
            
            # Store in cache and return
            _low_level_client_cache[cache_key] = client
            return client
    
    def _deduplicate_by_url(self, results: List[List[str]]) -> List[List[str]]:
        """
        Deduplicate and merge search results by URL (B+ merge).

        When multiple chunks from the same article URL appear in results,
        they are merged into a single result:
        - Best chunk (highest score) is used as the base
        - Summaries are merged with dedup (headline kept once, sentences deduped)
        - Metadata (_chunk_ids, _chunk_count, _best_chunk_name) is preserved

        Args:
            results: List of search results from multiple endpoints

        Returns:
            Deduplicated and merged list of results
        """
        # Group results by URL
        url_groups: dict = {}  # url -> list of results
        url_order: list = []   # preserve first-seen order

        for result in results:
            if isinstance(result, dict):
                url = result.get('url', '')
            elif isinstance(result, (list, tuple)) and len(result) >= 1:
                url = result[0]
            else:
                continue

            if not url:
                continue

            if url not in url_groups:
                url_groups[url] = []
                url_order.append(url)
            url_groups[url].append(result)

        # Merge each group
        merged_results = []
        for url in url_order:
            group = url_groups[url]
            if len(group) == 1:
                merged_results.append(group[0])
                continue

            # Multiple chunks from the same article — merge (B+)
            merged = self._merge_chunk_group(group)
            merged_results.append(merged)

        return merged_results

    @staticmethod
    def _merge_chunk_group(group: list) -> Any:
        """Merge a group of chunks from the same article URL (B+ strategy).

        Strategy:
        - Use the chunk with the longest name/content as the base
        - Merge summaries: keep headline once, deduplicate sentences
        - Preserve _chunk_ids, _chunk_count, _best_chunk_name
        """
        # Determine format (dict vs tuple)
        is_dict = isinstance(group[0], dict)

        if is_dict:
            # Find best chunk (longest name/title)
            best = max(group, key=lambda r: len(r.get('title', '') or r.get('name', '')))
            best = dict(best)  # copy

            # Collect all names for merging
            all_names = [r.get('title', '') or r.get('name', '') for r in group]
            merged_name = VectorDBClient._merge_summaries(all_names, max_length=500)

            # Fallback if all chunks have empty title/name
            if not merged_name:
                url = best.get('url', '')
                from urllib.parse import urlparse
                path = urlparse(url).path
                merged_name = path.split('/')[-1] or url[:100] or 'Untitled'
                logger.warning(f"All chunks have empty title, using fallback: {merged_name[:50]}")

            best_original_name = best.get('title', '') or best.get('name', '')
            best['title'] = merged_name
            best['name'] = merged_name
            best['_best_chunk_name'] = best_original_name
            best['_chunk_ids'] = [r.get('chunk_id', '') for r in group if r.get('chunk_id')]
            best['_chunk_count'] = len(group)
        else:
            # Tuple/list format: [url, json, name, site, ...]
            best_idx = max(
                range(len(group)),
                key=lambda i: len(group[i][2]) if len(group[i]) > 2 else 0
            )
            best = list(group[best_idx])  # copy

            all_names = [r[2] if len(r) > 2 else "" for r in group]
            merged_name = VectorDBClient._merge_summaries(all_names, max_length=500)

            # Fallback if all chunks have empty names
            if not merged_name and len(best) > 0:
                merged_name = best[0][:100] if best[0] else 'Untitled'

            if len(best) > 2:
                best[2] = merged_name

        return best

    @staticmethod
    def _merge_summaries(summaries: List[str], max_length: int = 500) -> str:
        """Merge chunk summaries with B+ dedup strategy.

        - All chunk summaries start with the headline — keep it once
        - Collect representative sentences from each chunk, deduplicate
        - Cap at max_length characters
        """
        if not summaries:
            return ""
        if len(summaries) == 1:
            return summaries[0]

        # Extract headline from the first summary (text before first period)
        first = summaries[0]
        headline = ""
        headline_end = first.find("。")
        if headline_end > 0:
            headline = first[:headline_end + 1]

        # Collect unique sentences from all summaries
        unique_sentences = []
        seen = set()

        for summary in summaries:
            # Remove headline prefix if present
            text = summary
            if headline and text.startswith(headline):
                text = text[len(headline):]

            # Split into sentences
            for sent in text.split("。"):
                sent = sent.strip()
                if sent and len(sent) > 5 and sent not in seen:
                    seen.add(sent)
                    unique_sentences.append(sent)

        # Build merged summary
        merged = headline
        for sent in unique_sentences:
            candidate = merged + sent + "。"
            if len(candidate) > max_length:
                break
            merged = candidate

        return merged
    
    def _aggregate_results(self, endpoint_results: Dict[str, List[List[str]]]) -> List[List[str]]:
        """
        Aggregate results from multiple endpoints, merging JSON data for duplicate URLs.
        
        When the same URL appears in multiple endpoints, the JSON data (second element)
        from each source is merged into a single array.
        
        Args:
            endpoint_results: Dictionary mapping endpoint names to their results
            
        Returns:
            Aggregated results with merged JSON for duplicate URLs
        """
        # Dictionary to store aggregated data by URL
        # Format: {url: {"result": [url, json_array, name, site], "sources": [json1, json2...]}}
        url_to_data = {}
        
        # First pass: collect all results and group by URL
        for endpoint_name, results in endpoint_results.items():
            if results:
                logger.debug(f"Got {len(results)} results from {endpoint_name}")

                for result in results:
                    # Handle both Dict (new format) and Tuple (legacy format)
                    if isinstance(result, dict):
                        # New Dict format
                        url = result.get('url', '')
                        json_data = result.get('schema_json', '')
                        name = result.get('title', '')
                        site = result.get('site', '')
                        vector = result.get('vector')
                    elif len(result) >= 4:
                        # Legacy Tuple format: [url, json, name, site] or [url, json, name, site, vector]
                        url = result[0]
                        json_data = result[1]
                        name = result[2]
                        site = result[3]
                        vector = result[4] if len(result) == 5 else None
                    else:
                        continue  # Skip invalid results

                    if url:
                        if url not in url_to_data:
                            # First occurrence of this URL
                            url_to_data[url] = {
                                "url": url,
                                "json_list": [json_data] if json_data else [],
                                "name": name,
                                "site": site,
                                "vector": vector,  # Store vector from first occurrence
                                "first_endpoint": endpoint_name
                            }
                        else:
                            # URL already seen, append JSON data
                            if json_data:
                                url_to_data[url]["json_list"].append(json_data)
                            # Keep vector from first occurrence (don't overwrite)
        
        # Second pass: build final results maintaining order from first endpoint
        # We'll interleave to preserve relevance ordering
        final_results = []
        seen_urls = set()
        
        # Create iterators for each endpoint's results
        iterators = {}
        for endpoint_name, results in endpoint_results.items():
            if results:
                iterators[endpoint_name] = iter(results)
        
        # Interleave results to maintain relevance ordering
        while iterators:
            endpoints_to_remove = []
            
            for endpoint_name, iterator in iterators.items():
                try:
                    result = next(iterator)
                    # Handle both Dict and Tuple formats
                    if isinstance(result, dict):
                        url = result.get('url', '')
                    elif len(result) >= 1:
                        url = result[0]
                    else:
                        url = None

                    if url and url not in seen_urls:
                            seen_urls.add(url)
                            # Get the aggregated data for this URL
                            data = url_to_data.get(url)
                            if data:
                                # Merge JSON data if multiple sources
                                json_list = data["json_list"]
                                if len(json_list) > 1:
                                    # Multiple sources - merge them
                                    merged_json = merge_json_array(json_list)
                                    # Convert back to JSON string
                                    merged_json_str = json.dumps(merged_json)
                                else:
                                    # Single source - use as is
                                    merged_json_str = json_list[0] if json_list else "{}"
                                
                                # Create result with merged JSON
                                merged_result = [
                                    data["url"],
                                    merged_json_str,  # Single merged JSON string
                                    data["name"],
                                    data["site"]
                                ]
                                # Preserve vector if present
                                if data.get("vector") is not None:
                                    merged_result.append(data["vector"])
                                final_results.append(merged_result)
                except StopIteration:
                    endpoints_to_remove.append(endpoint_name)
            
            # Remove exhausted iterators
            for endpoint in endpoints_to_remove:
                del iterators[endpoint]
        
        # Calculate total results safely
        total_results = sum(len(r) for r in endpoint_results.values() if r is not None)
        logger.info(f"Aggregated {total_results} total results into {len(final_results)} unique URLs")
        
        return final_results
    
    async def delete_documents_by_site(self, site: str, **kwargs) -> int:
        """
        Delete all documents matching the specified site.
        
        Args:
            site: Site identifier
            **kwargs: Additional parameters
            
        Returns:
            Number of documents deleted
        """
        if not self.write_endpoint:
            raise ValueError("No write endpoint configured for delete operations")
            
        async with self._retrieval_lock:
            logger.info(f"Deleting documents for site: {site} using write endpoint: {self.write_endpoint}")
            
            try:
                client = await self.get_client(self.write_endpoint)
                count = await client.delete_documents_by_site(site, **kwargs)
                logger.info(f"Successfully deleted {count} documents for site: {site}")
                return count
            except Exception as e:
                logger.exception(f"Error deleting documents for site {site}: {e}")
                logger.log_with_context(
                    LogLevel.ERROR,
                    "Document deletion failed",
                    {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "site": site,
                        "endpoint": self.write_endpoint
                    }
                )
                raise
    
    async def upload_documents(self, documents: List[Dict[str, Any]], **kwargs) -> int:
        """
        Upload documents to the database.
        
        Args:
            documents: List of document objects
            **kwargs: Additional parameters
            
        Returns:
            Number of documents uploaded
        """
        if not self.write_endpoint:
            raise ValueError("No write endpoint configured for upload operations")
            
        async with self._retrieval_lock:
            logger.info(f"Uploading {len(documents)} documents to write endpoint: {self.write_endpoint}")
            
            try:
                client = await self.get_client(self.write_endpoint)
                count = await client.upload_documents(documents, **kwargs)
                logger.info(f"Successfully uploaded {count} documents")
                return count
            except Exception as e:
                logger.exception(f"Error uploading documents: {e}")
                logger.log_with_context(
                    LogLevel.ERROR,
                    "Document upload failed",
                    {
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                        "document_count": len(documents),
                        "endpoint": self.write_endpoint
                    }
                )
                raise
    
    async def search(self, query: str, site: Union[str, List[str]], 
                    num_results: int = 50, endpoint_name: Optional[str] = None, **kwargs) -> List[List[str]]:
        """
        Search for documents matching the query and site.
        
        Args:
            query: Search query string
            site: Site identifier or list of sites
            num_results: Maximum number of results to return
            endpoint_name: Optional endpoint name override
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        # Handle configured sites
        if site == "all":
            sites = CONFIG.nlweb.sites
            if sites and sites != "all":
                # Use configured sites instead of "all"
                site = sites

        # If specific endpoint is requested, use only that endpoint
        if endpoint_name:
            if endpoint_name not in CONFIG.retrieval_endpoints:
                raise ValueError(f"Invalid endpoint: {endpoint_name}")
            temp_client = VectorDBClient(endpoint_name=endpoint_name)
            return await temp_client.search(query, site, num_results, **kwargs)
        
        # Process site parameter for consistency
        if isinstance(site, str) and ',' in site:
            site = site.replace('[', '').replace(']', '')
            site = [s.strip() for s in site.split(',')]
        elif isinstance(site, str):
            site = site.replace(" ", "_")

        logger.info(f"Searching for '{query[:50]}...' in site: {site}, num_results: {num_results}")
        logger.info(f"Querying {len(self.enabled_endpoints)} enabled endpoints in parallel")
        start_time = time.time()

        # Create tasks for parallel queries to endpoints that have the requested site
        tasks = []
        endpoint_names = []
        skipped_endpoints = []

        for endpoint_name in self.enabled_endpoints:
            try:
                client = await self.get_client(endpoint_name)

                # If only one endpoint is enabled (e.g., explicit db= parameter), skip can_handle_query check
                if len(self.enabled_endpoints) == 1:
                    # Single endpoint mode - use it regardless of can_handle_query
                    logger.info(f"Single endpoint mode for {endpoint_name}, skipping can_handle_query check")
                else:
                    # Check if the provider can handle this query
                    if not await client.can_handle_query(site, **kwargs):
                        skipped_endpoints.append(endpoint_name)
                        continue

                # Use search_all_sites if site is "all"
                if site == "all":
                    task = asyncio.create_task(client.search_all_sites(query, num_results, **kwargs))
                else:
                    # Pass all arguments including handler to all clients
                    # Individual clients can choose to use or ignore the handler
                    task = asyncio.create_task(client.search(query, site, num_results, **kwargs))
                tasks.append(task)
                endpoint_names.append(endpoint_name)
            except Exception as e:
                logger.warning(f"Failed to create search task for endpoint {endpoint_name}: {e}")

        if skipped_endpoints:
            logger.debug(f"Skipped endpoints without site '{site}': {skipped_endpoints}")

        if not tasks:
            raise ValueError("No valid endpoints available for search")

        # Execute all searches in parallel and collect results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results and handle failures gracefully
        endpoint_results = {}
        successful_endpoints = 0

        for endpoint_name, result in zip(endpoint_names, results):
            if isinstance(result, Exception):
                logger.warning(f"Search failed for endpoint {endpoint_name}: {result}")
            elif result is None:
                logger.warning(f"Endpoint {endpoint_name} returned None, treating as empty results")
                endpoint_results[endpoint_name] = []
            else:
                endpoint_results[endpoint_name] = result
                successful_endpoints += 1

        if successful_endpoints == 0:
            raise ValueError("All endpoint searches failed")

        # Aggregate and deduplicate results
        final_results = self._aggregate_results(endpoint_results)

        # Limit to requested number of results
        # Results are already in relevance order from aggregation
        final_results = final_results[:num_results]

        end_time = time.time()
        search_duration = end_time - start_time

        logger.log_with_context(
            LogLevel.INFO,
            "Parallel search completed",
            {
                "duration": f"{search_duration:.2f}s",
                "endpoints_queried": len(tasks),
                "endpoints_succeeded": successful_endpoints,
                "total_results": len(final_results),
                "site": site
            }
        )

        return final_results
    
    async def search_by_url(self, url: str, endpoint_name: Optional[str] = None, **kwargs) -> Optional[List[str]]:
        """
        Retrieve a document by its exact URL.
        
        Args:
            url: URL to search for
            endpoint_name: Optional endpoint name override
            **kwargs: Additional parameters
            
        Returns:
            Document data or None if not found
        """
        # If endpoint is specified and different from current, create a new client for that endpoint
        if endpoint_name and endpoint_name != self.endpoint_name:
            temp_client = VectorDBClient(endpoint_name=endpoint_name)
            return await temp_client.search_by_url(url, **kwargs)
        
        logger.info(f"Retrieving item with URL: {url}")

        try:
            # For single endpoint mode, use the first (and only) endpoint
            if self.endpoint_name:
                client = await self.get_client(self.endpoint_name)
            else:
                # Multiple endpoints - need to search all of them
                for endpoint_name in self.enabled_endpoints:
                    try:
                        client = await self.get_client(endpoint_name)
                        result = await client.search_by_url(url, **kwargs)
                        if result:
                            return result
                    except Exception as e:
                        logger.warning(f"Failed to search by URL in endpoint {endpoint_name}: {e}")
                return None

            result = await client.search_by_url(url, **kwargs)

            if result:
                logger.debug(f"Successfully retrieved item for URL: {url}")
            else:
                logger.warning(f"No item found for URL: {url}")

            return result
        except Exception as e:
            logger.exception(f"Error retrieving item with URL: {url}")
            logger.log_with_context(
                LogLevel.ERROR,
                "Item retrieval failed",
                {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "url": url,
                    "db_type": self.db_type,
                    "endpoint": self.endpoint_name
                }
            )
            raise
    
    async def search_all_sites(self, query: str, num_results: int = 50, 
                             endpoint_name: Optional[str] = None, **kwargs) -> List[List[str]]:
        """
        Search across all sites.
        
        Args:
            query: Search query string
            num_results: Maximum number of results to return
            endpoint_name: Optional endpoint name override
            **kwargs: Additional parameters
            
        Returns:
            List of search results
        """
        # Just call search with "all" as the site parameter
        # The individual clients will handle "all" appropriately
        return await self.search(query, "all", num_results, endpoint_name, **kwargs)
    
    async def get_sites(self, endpoint_name: Optional[str] = None, **kwargs) -> List[str]:
        """
        Get list of all sites available in the database.
        
        For backends that don't support get_sites, returns an empty list to indicate
        that the backend should be queried for all sites.
        
        Args:
            endpoint_name: Optional endpoint name override
            **kwargs: Additional parameters
            
        Returns:
            List of site names, or empty list if backend doesn't support get_sites
        """
        
        # If endpoint is specified and different from current, create a new client for that endpoint
        if endpoint_name and endpoint_name != self.endpoint_name:
            temp_client = VectorDBClient(endpoint_name=endpoint_name)
            return await temp_client.get_sites(**kwargs)
        
        logger.info("Retrieving list of sites from database")

        try:
            # For single endpoint mode, use the first (and only) endpoint
            if self.endpoint_name:
                client = await self.get_client(self.endpoint_name)
                sites = await client.get_sites(**kwargs)
            else:
                # Multiple endpoints - aggregate sites from all
                all_sites = set()
                for endpoint_name in self.enabled_endpoints:
                    try:
                        client = await self.get_client(endpoint_name)
                        endpoint_sites = await client.get_sites(**kwargs)
                        if endpoint_sites:  # Not None and not empty
                            all_sites.update(endpoint_sites)
                    except Exception as e:
                        logger.warning(f"Failed to get sites from endpoint {endpoint_name}: {e}")
                sites = list(all_sites)

            # If backend doesn't support get_sites, it should return None
            if sites is None:
                # Return empty list to indicate unknown sites
                logger.info(f"Backend doesn't support get_sites, will query for all sites")
                return []

            logger.log_with_context(
                LogLevel.INFO,
                "Sites retrieved",
                {
                    "sites_count": len(sites),
                    "db_type": self.db_type,
                    "endpoint": self.endpoint_name
                }
            )
            return sites
        except Exception as e:
            # Backend doesn't support get_sites or error occurred
            logger.info(f"Backend doesn't support get_sites or error occurred: {e}")

            # Return empty list to indicate unknown sites (will be queried for all)
            logger.log_with_context(
                LogLevel.INFO,
                "Backend doesn't support get_sites, will query for all sites",
                {
                    "db_type": self.db_type,
                    "endpoint": self.endpoint_name,
                    "error": str(e)
                }
            )
            return []


# Factory function to make it easier to get a client with the right type
def get_vector_db_client(endpoint_name: Optional[str] = None, 
                        query_params: Optional[Dict[str, Any]] = None) -> VectorDBClient:
    """
    Factory function to create a vector database client with the appropriate configuration.
    Uses a global cache to avoid repeated initialization and site queries.
    
    Args:
        endpoint_name: Optional name of the endpoint to use
        query_params: Optional query parameters for overriding endpoint
        
    Returns:
        Configured VectorDBClient instance (cached if possible)
    """
    global _vector_client_cache

    # Create a cache key based on endpoint_name and query_params
    base_key = endpoint_name or 'default'
    if query_params:
        params_key = "_".join(f"{k}={v}" for k, v in sorted(query_params.items()) if isinstance(v, (str, int, float, bool)))
        cache_key = f"{base_key}_{params_key}" if params_key else base_key
    else:
        cache_key = base_key

    # Check if we have a cached client
    if cache_key in _vector_client_cache:
        return _vector_client_cache[cache_key]

    # Create a new client and cache it
    client = VectorDBClient(endpoint_name=endpoint_name, query_params=query_params)
    _vector_client_cache[cache_key] = client

    return client




async def search(query: str,
                site: str = "all",
                num_results: int = 50,
                endpoint_name: Optional[str] = None,
                query_params: Optional[Dict[str, Any]] = None,
                handler: Optional[Any] = None,
                filters: Optional[List[Dict[str, Any]]] = None,
                **kwargs) -> List[Dict[str, Any]]:
    """
    Simplified search interface that combines client creation and search in one call.

    Args:
        query: The search query
        site: Site to search in (default: "all")
        num_results: Number of results to return (default: 10)
        endpoint_name: Optional name of the endpoint to use
        query_params: Optional query parameters for overriding endpoint
        handler: Optional handler with http_handler for sending messages
        filters: Optional list of generic filters, e.g.
            [{"field": "datePublished", "operator": "gte", "value": "2026-01-01"}]
            Each provider converts these to its native filter format.
        **kwargs: Additional parameters passed to the search method

    Returns:
        List of search results

    Example:
        results = await search("climate change", site="example.com", num_results=5)
    """
    client = get_vector_db_client(endpoint_name=endpoint_name, query_params=query_params)
    # Pass handler and filters through kwargs if provided
    if handler:
        kwargs['handler'] = handler
    if filters:
        kwargs['filters'] = filters

    results = await client.search(query, site, num_results, **kwargs)
    
    # Send retrieval count message if handler is provided and in debug mode
    if handler and getattr(handler, 'debug_mode', False) and hasattr(handler, 'http_handler') and hasattr(handler.http_handler, 'write_stream'):
        retrieval_message = {
            "message_type": "retrieval_count",
            "query": query,
            "site": site,
            "count": len(results),
            "requested_count": num_results,
            "sender_info": {"id": "system", "name": "NLWeb"}
        }
        try:
            await handler.http_handler.write_stream(retrieval_message)
            logger.info(f"Sent retrieval count message: {len(results)} results for query '{query}' on site '{site}'")
        except Exception as e:
            logger.warning(f"Failed to send retrieval count message: {e}")
    
    return results


def _extract_url(item) -> Optional[str]:
    """Extract URL from a search result item (Dict or Tuple format)."""
    if isinstance(item, dict):
        return item.get('url')
    elif isinstance(item, (list, tuple)) and len(item) >= 1:
        return item[0]
    return None


async def search_with_expansion(
    query: str,
    expansion_queries: List[str],
    site: str = "all",
    num_results: int = 50,
    num_per_expansion: int = 20,
    endpoint_name: Optional[str] = None,
    query_params: Optional[Dict[str, Any]] = None,
    handler: Optional[Any] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Search with the original query plus expansion queries in parallel.
    Original query results are prioritised; expansion results are appended
    after URL-based deduplication.

    Args:
        query: The primary search query
        expansion_queries: Additional queries from QueryRewrite expansion
        site: Site to search in
        num_results: Max results for the primary query
        num_per_expansion: Max results per expansion query
        endpoint_name: Optional endpoint override
        query_params: Optional query parameters
        handler: Optional handler for messaging
        filters: Optional generic filters (time range, author, etc.)
        **kwargs: Additional parameters

    Returns:
        Deduplicated list of search results, primary results first.
    """
    # Pre-compute all embeddings in a single batch API call to avoid N+1 serial embedding
    # requests.  Providers that expose a batch endpoint (e.g. OpenRouter, OpenAI) send all
    # texts in one HTTP round-trip, reducing latency from N×T to T.
    all_queries = [query] + list(expansion_queries)
    precomputed_embeddings: List[Optional[List[float]]] = [None] * len(all_queries)
    try:
        embed_start = time.time()
        batch_results = await batch_get_embeddings(all_queries, timeout=60)
        logger.info(
            f"[EXPANSION] Batch embedding: {len(all_queries)} queries in "
            f"{time.time() - embed_start:.2f}s"
        )
        precomputed_embeddings = batch_results
    except Exception as e:
        # Graceful degradation: fall back to per-query embedding inside each search()
        logger.warning(
            f"[EXPANSION] Batch embedding failed ({type(e).__name__}: {e}), "
            "falling back to per-query embedding"
        )

    # Primary search gets handler (may mutate handler.time_filter_relaxed etc.)
    primary_kwargs = dict(
        site=site,
        endpoint_name=endpoint_name,
        query_params=query_params,
        handler=handler,
        filters=filters,
        precomputed_embedding=precomputed_embeddings[0],
        **kwargs
    )
    # Expansion searches omit handler to avoid concurrent mutations
    expansion_kwargs_list = [
        dict(
            site=site,
            endpoint_name=endpoint_name,
            query_params=query_params,
            filters=filters,
            precomputed_embedding=precomputed_embeddings[i + 1],
            **kwargs
        )
        for i in range(len(expansion_queries))
    ]

    # Launch all searches in parallel — embeddings already computed, only DB work remains
    primary_task = asyncio.create_task(
        search(query, num_results=num_results, **primary_kwargs)
    )
    expansion_tasks = [
        asyncio.create_task(
            search(eq, num_results=num_per_expansion, **expansion_kwargs_list[i])
        )
        for i, eq in enumerate(expansion_queries)
    ]

    all_results = await asyncio.gather(primary_task, *expansion_tasks, return_exceptions=True)

    # Primary results first
    primary_items = all_results[0] if not isinstance(all_results[0], Exception) else []
    if isinstance(all_results[0], Exception):
        logger.warning(f"Primary search failed: {all_results[0]}")

    # Deduplicate: primary results take priority
    seen_urls = set()
    merged = []

    for item in primary_items:
        url = _extract_url(item)
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(item)

    # Append expansion results, skipping duplicates
    for i, result in enumerate(all_results[1:]):
        if isinstance(result, Exception):
            logger.warning(f"Expansion search failed for '{expansion_queries[i]}': {result}")
            continue
        for item in result:
            url = _extract_url(item)
            if url and url not in seen_urls:
                seen_urls.add(url)
                merged.append(item)

    logger.info(
        f"search_with_expansion: primary={len(primary_items)}, "
        f"expansions={len(expansion_queries)}, "
        f"merged={len(merged)} (after dedup)"
    )

    return merged


async def search_all_sites(query: str,
                          top_n: int = 10,
                          endpoint_name: Optional[str] = None,
                          query_params: Optional[Dict[str, Any]] = None,
                          **kwargs) -> List[Dict[str, Any]]:
    """
    Search across all sites using a simplified interface.
    
    Args:
        query: The search query
        top_n: Number of results to return (default: 10)
        endpoint_name: Optional name of the endpoint to use
        query_params: Optional query parameters for overriding endpoint
        **kwargs: Additional parameters passed to the search_all_sites method
        
    Returns:
        List of search results from all sites
        
    Example:
        results = await search_all_sites("climate change", top_n=20)
    """
    client = get_vector_db_client(endpoint_name=endpoint_name, query_params=query_params)
    return await client.search_all_sites(query, top_n, **kwargs)


async def get_sites(endpoint_name: Optional[str] = None,
                   query_params: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Get list of available sites from the database.
    
    Args:
        endpoint_name: Optional name of the endpoint to use
        query_params: Optional query parameters for overriding endpoint
        
    Returns:
        List of site names available in the database
        
    Example:
        sites = await get_sites()
    """
    client = get_vector_db_client(endpoint_name=endpoint_name, query_params=query_params)
    return await client.get_sites()


async def search_by_url(url: str,
                       endpoint_name: Optional[str] = None,
                       query_params: Optional[Dict[str, Any]] = None,
                       **kwargs) -> Optional[List[str]]:
    """
    Retrieve a document by its exact URL.
    
    Args:
        url: URL to search for
        endpoint_name: Optional name of the endpoint to use
        query_params: Optional query parameters for overriding endpoint
        **kwargs: Additional parameters passed to the search_by_url method
        
    Returns:
        Document data or None if not found
        
    Example:
        document = await search_by_url("https://example.com/article")
    """
    client = get_vector_db_client(endpoint_name=endpoint_name, query_params=query_params)
    return await client.search_by_url(url, **kwargs)


async def upload_documents(documents: List[Dict[str, Any]],
                          endpoint_name: Optional[str] = None,
                          query_params: Optional[Dict[str, Any]] = None,
                          **kwargs) -> int:
    """
    Upload documents to the database using the configured write endpoint.
    
    Args:
        documents: List of document objects to upload
        endpoint_name: Optional name of the endpoint to use (overrides write_endpoint)
        query_params: Optional query parameters for overriding endpoint
        **kwargs: Additional parameters passed to the upload_documents method
        
    Returns:
        Number of documents uploaded
        
    Example:
        count = await upload_documents(documents)
    """
    client = get_vector_db_client(endpoint_name=endpoint_name, query_params=query_params)
    return await client.upload_documents(documents, **kwargs)


async def delete_documents_by_site(site: str,
                                  endpoint_name: Optional[str] = None,
                                  query_params: Optional[Dict[str, Any]] = None,
                                  **kwargs) -> int:
    """
    Delete all documents for a specific site from the database.
    
    Args:
        site: Site identifier to delete documents for
        endpoint_name: Optional name of the endpoint to use (overrides write_endpoint)
        query_params: Optional query parameters for overriding endpoint
        **kwargs: Additional parameters passed to the delete_documents_by_site method
        
    Returns:
        Number of documents deleted
        
    Example:
        count = await delete_documents_by_site("example.com")
    """
    client = get_vector_db_client(endpoint_name=endpoint_name, query_params=query_params)
    return await client.delete_documents_by_site(site, **kwargs)