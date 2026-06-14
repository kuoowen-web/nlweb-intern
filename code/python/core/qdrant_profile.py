"""
qdrant_profile.py - Qdrant Profile Manager

Provides profile-based switching between online/offline Qdrant configurations.
Set QDRANT_PROFILE=online|offline to activate a profile.
When unset, all behavior is backward compatible.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

# Module-level cache
_active_profile: Optional["QdrantProfile"] = None
_profile_loaded: bool = False


@dataclass
class QdrantProfile:
    """A Qdrant configuration profile bundling connection + embedding settings."""
    name: str
    description: str
    qdrant_url: str
    qdrant_api_key: Optional[str]
    collection: str
    embedding_provider: str
    embedding_model: str
    dimension: int
    retrieval_endpoint: str


def load_qdrant_profile(config_dir: str) -> Optional[QdrantProfile]:
    """
    Load the active Qdrant profile based on QDRANT_PROFILE env var.

    Args:
        config_dir: Path to the config directory containing config_qdrant_profiles.yaml

    Returns:
        QdrantProfile if QDRANT_PROFILE is set, None otherwise
    """
    global _active_profile, _profile_loaded

    if _profile_loaded:
        return _active_profile

    profile_name = os.environ.get("QDRANT_PROFILE")
    if not profile_name:
        _profile_loaded = True
        _active_profile = None
        return None

    profile_path = os.path.join(config_dir, "config_qdrant_profiles.yaml")
    if not os.path.exists(profile_path):
        raise FileNotFoundError(
            f"QDRANT_PROFILE={profile_name} is set, but {profile_path} not found"
        )

    with open(profile_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    profiles = data.get("profiles", {})
    if profile_name not in profiles:
        available = ", ".join(profiles.keys()) or "(none)"
        raise ValueError(
            f"Unknown QDRANT_PROFILE='{profile_name}'. Available: {available}"
        )

    cfg = profiles[profile_name]
    qdrant_cfg = cfg.get("qdrant", {})
    embedding_cfg = cfg.get("embedding", {})

    # Resolve env vars for Qdrant connection
    url_env = qdrant_cfg.get("url_env", "QDRANT_URL")
    key_env = qdrant_cfg.get("api_key_env", "QDRANT_API_KEY")
    qdrant_url = os.environ.get(url_env, "http://localhost:6333")
    qdrant_api_key = os.environ.get(key_env)

    profile = QdrantProfile(
        name=profile_name,
        description=cfg.get("description", ""),
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        collection=qdrant_cfg.get("collection", "nlweb_collection"),
        embedding_provider=embedding_cfg.get("provider", "openai"),
        embedding_model=embedding_cfg.get("model", "text-embedding-3-small"),
        dimension=embedding_cfg.get("dimension", 1536),
        retrieval_endpoint=cfg.get("retrieval_endpoint", "qdrant_url"),
    )

    _active_profile = profile
    _profile_loaded = True

    hostname = urlparse(profile.qdrant_url).hostname or profile.qdrant_url
    logger.info(
        f"[QDRANT PROFILE] Active: {profile.name} | "
        f"collection={profile.collection} | "
        f"embedding={profile.embedding_provider}/{profile.embedding_model} ({profile.dimension}D) | "
        f"host={hostname}"
    )

    return profile


def get_active_profile() -> Optional[QdrantProfile]:
    """Return the cached active profile, or None if no profile is active."""
    global _active_profile, _profile_loaded

    if not _profile_loaded:
        # Profile hasn't been loaded yet; try loading from default config dir
        config_dir = os.environ.get("NLWEB_CONFIG_DIR")
        if not config_dir:
            # Derive from code structure: code/python/core/ -> ../../config
            code_dir = os.path.dirname(os.path.abspath(__file__))
            config_dir = os.path.join(code_dir, "..", "..", "..", "config")
            config_dir = os.path.normpath(config_dir)
        try:
            load_qdrant_profile(config_dir)
        except Exception as e:
            logger.warning(f"Failed to load Qdrant profile: {e}")
            _profile_loaded = True
            _active_profile = None

    return _active_profile


def apply_profile_to_config(profile: QdrantProfile, config) -> None:
    """
    Apply a Qdrant profile to the AppConfig instance.

    Overrides:
    - preferred_embedding_provider
    - retrieval endpoint's index_name, api_endpoint, api_key
    - write_endpoint

    Args:
        profile: The QdrantProfile to apply
        config: The AppConfig instance
    """
    # Override embedding provider
    config.preferred_embedding_provider = profile.embedding_provider
    logger.info(f"[QDRANT PROFILE] Embedding provider -> {profile.embedding_provider}")

    # Override the retrieval endpoint
    endpoint = config.retrieval_endpoints.get(profile.retrieval_endpoint)
    if endpoint:
        endpoint.index_name = profile.collection
        endpoint.enabled = True
        if profile.qdrant_url:
            endpoint.api_endpoint = profile.qdrant_url
        if profile.qdrant_api_key:
            endpoint.api_key = profile.qdrant_api_key
        logger.info(f"[QDRANT PROFILE] Endpoint '{profile.retrieval_endpoint}' -> collection='{profile.collection}'")
    else:
        logger.warning(
            f"[QDRANT PROFILE] Endpoint '{profile.retrieval_endpoint}' not found in retrieval config"
        )

    # Set write endpoint
    config.write_endpoint = profile.retrieval_endpoint
    logger.info(f"[QDRANT PROFILE] write_endpoint -> '{profile.retrieval_endpoint}'")

    # Validate dimension compatibility
    _validate_collection_dimension(profile)


def _validate_collection_dimension(profile: QdrantProfile) -> None:
    """
    Check that the target collection's vector dimension matches the profile.

    Raises ValueError on dimension mismatch (blocks startup).
    Logs a warning and continues if Qdrant is unreachable or collection doesn't exist.
    """
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(
            url=profile.qdrant_url,
            api_key=profile.qdrant_api_key,
            timeout=10,
        )
        try:
            if not client.collection_exists(profile.collection):
                logger.info(
                    f"[QDRANT PROFILE] Collection '{profile.collection}' does not exist yet. "
                    f"Will be created with {profile.dimension}D vectors."
                )
                return

            info = client.get_collection(profile.collection)
            actual_dim = info.config.params.vectors.size
        finally:
            client.close()

        if actual_dim != profile.dimension:
            error_msg = (
                f"DIMENSION MISMATCH: Collection '{profile.collection}' has {actual_dim}D vectors, "
                f"but profile '{profile.name}' expects {profile.dimension}D "
                f"(embedding: {profile.embedding_provider}/{profile.embedding_model}). "
                f"Indexing and search will fail!"
            )
            logger.error(f"[QDRANT PROFILE] {error_msg}")
            raise ValueError(error_msg)

        logger.info(
            f"[QDRANT PROFILE] Dimension check OK: "
            f"'{profile.collection}' = {actual_dim}D (matches profile)"
        )

    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"[QDRANT PROFILE] Could not validate dimension: {e}")


def get_active_qdrant_config():
    """
    Return a QdrantConfig for the indexing pipeline based on the active profile.

    Returns:
        QdrantConfig if a profile is active, None otherwise (caller falls back to env vars)
    """
    profile = get_active_profile()
    if not profile:
        return None

    # Lazy import to avoid circular dependency
    from indexing.qdrant_uploader import QdrantConfig

    return QdrantConfig(
        url=profile.qdrant_url,
        api_key=profile.qdrant_api_key,
        collection_name=profile.collection,
    )
