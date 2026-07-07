"""
Source tier filter for implementing tier-based filtering and content enrichment.
"""

from typing import List, Dict, Any
from core.config import CONFIG


class NoValidSourcesError(Exception):
    """Raised when all sources are filtered out in strict mode."""
    pass


class SourceTierFilter:
    """
    Hard filter implementing tier-based filtering and content enrichment.

    Filters sources based on mode configuration and enriches items
    with tier metadata and prefixes.
    """

    def __init__(self, source_tiers: Dict[str, Dict[str, Any]]):
        """
        Initialize source tier filter.

        Args:
            source_tiers: Dictionary mapping source names to tier info
                         (from CONFIG.reasoning_source_tiers)
        """
        self.source_tiers = source_tiers

    def _extract_site(self, item: Any) -> str:
        """
        Extract site name from item regardless of format.

        Args:
            item: Item in dict or tuple/list format

        Returns:
            Site name string
        """
        if isinstance(item, dict):
            return (item.get("source") or item.get("site") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) > 3:
            return item[3].strip() if item[3] else ""
        else:
            return ""

    def filter_and_enrich(
        self,
        items: List[Dict[str, Any]],
        mode: str
    ) -> List[Dict[str, Any]]:
        """
        Pass-through no-op (source tier mechanism removed 2026-06).

        The Tier 1-5 authority-grading enrichment ([Tier N | type] prefix +
        _reasoning_metadata) has been removed. Items are now returned unchanged;
        the prompt instructions guide the LLM to apply non-graded source handling.
        The 'mode' parameter is kept for signature compatibility but is not used.

        Note: Tier 6 provenance markers ([Tier 6 | ...]) are NOT produced here —
        they are built independently by the orchestrator / loop_engine and are
        unaffected by this pass-through.

        Args:
            items: List of retrieved items (NLWeb Item format)
            mode: Research mode (kept for signature compatibility, value ignored)

        Returns:
            The items unchanged (no enrichment, no hard filtering).

        Raises:
            NoValidSourcesError: If no items are available at all.
        """
        # Empty-source guardrail retained (upstream issue, not mode filtering).
        if not items:
            raise NoValidSourcesError("No valid sources available")

        return items

    def _get_tier_info(self, source: str) -> Dict[str, Any]:
        """
        Get tier and type information for a source.

        Args:
            source: Source name

        Returns:
            Dictionary with "tier" and "type" keys
            Unknown sources get tier=999, type="unknown"
        """
        if source in self.source_tiers:
            return self.source_tiers[source]
        else:
            # Unknown source
            return {"tier": 999, "type": "unknown"}

    def _enrich_item(
        self,
        item: Dict[str, Any],
        tier: int,
        source_type: str,
        original_source: str
    ) -> Dict[str, Any]:
        """
        Enrich item with tier metadata and description prefix.

        Args:
            item: Original item (dict or tuple/list)
            tier: Source tier (1-5 or 999)
            source_type: Source type (official, news, digital, social, unknown)
            original_source: Original source name

        Returns:
            Enriched dict item with metadata and tier prefix
        """
        # Convert to dict if tuple/list format
        if isinstance(item, (list, tuple)):
            # Legacy tuple format: (url, schema_json, name, site, [vector])
            import json
            enriched = {
                "url": item[0] if len(item) > 0 else "",
                "title": item[2] if len(item) > 2 else "",
                "site": item[3] if len(item) > 3 else "",
            }
            # Extract description from schema_json
            try:
                schema_json = item[1] if len(item) > 1 else "{}"
                schema_obj = json.loads(schema_json) if isinstance(schema_json, str) else schema_json
                enriched["description"] = schema_obj.get("description", "")
            except:
                enriched["description"] = ""
        else:
            # Create a copy to avoid mutating original
            enriched = item.copy()

        # Add reasoning metadata
        enriched["_reasoning_metadata"] = {
            "tier": tier,
            "type": source_type,
            "original_source": original_source
        }

        # Add tier prefix to description
        tier_prefix = self._get_tier_prefix(tier, source_type)
        original_description = enriched.get("description", "")
        enriched["description"] = f"{tier_prefix} {original_description}".strip()

        return enriched

    def _get_tier_prefix(self, tier: int, source_type: str) -> str:
        """
        Generate tier prefix for content.

        Args:
            tier: Source tier
            source_type: Source type

        Returns:
            Tier prefix string (e.g., "[Tier 1 | official]")
        """
        if tier == 999:
            return "[Tier Unknown | unknown]"
        elif tier == 6:
            # Stage 5: Tier 6 for LLM Knowledge and Web Reference
            return f"[Tier 6 | {source_type}]"
        else:
            return f"[Tier {tier} | {source_type}]"

    def is_tier_6_source(self, item: Dict) -> bool:
        """
        Check if an item is a Tier 6 source (LLM Knowledge or Web Reference).

        Args:
            item: Item dict with _reasoning_metadata

        Returns:
            True if tier 6, False otherwise
        """
        metadata = item.get("_reasoning_metadata", {})
        return metadata.get("tier") == 6

    def get_tier_6_type(self, item: Dict) -> str:
        """
        Get the Tier 6 subtype (llm_knowledge or web_reference).

        Args:
            item: Item dict with _reasoning_metadata

        Returns:
            "llm_knowledge", "web_reference", or empty string
        """
        metadata = item.get("_reasoning_metadata", {})
        if metadata.get("tier") == 6:
            return metadata.get("type", "")
        return ""

    def get_tier(self, source: str) -> int:
        """
        Get tier number for a source.

        Args:
            source: Source name

        Returns:
            Tier number (1-5 or 999 for unknown)
        """
        tier_info = self._get_tier_info(source)
        return tier_info["tier"]
