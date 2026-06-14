# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
MMR (Maximal Marginal Relevance) Algorithm Implementation

Purpose:
    Diversify search results by balancing relevance and novelty.
    Prevents redundant results by penalizing documents similar to already-selected ones.

Algorithm:
    MMR = λ * Relevance(doc, query) - (1-λ) * max(Similarity(doc, selected_doc))

    Where:
    - λ (lambda): Trade-off parameter between relevance and diversity
      - λ = 1.0: Pure relevance (no diversity)
      - λ = 0.5: Balanced
      - λ = 0.0: Pure diversity (no relevance)
    - Relevance: Document's score from ranking (LLM score, BM25, etc.)
    - Similarity: Cosine similarity between document vectors

References:
    Carbonell, J., & Goldstein, J. (1998). "The use of MMR, diversity-based
    reranking for reordering documents and producing summaries."
"""

from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("mmr")


class MMRReranker:
    """
    Maximal Marginal Relevance (MMR) re-ranker for diversifying search results.
    """

    def __init__(self, lambda_param: float = 0.7, query: str = ""):
        """
        Initialize MMR re-ranker.

        Args:
            lambda_param: Trade-off between relevance and diversity (0.0 to 1.0)
                         Higher λ = more relevance, lower λ = more diversity
            query: The user's query (for intent detection)
        """
        self.lambda_param = lambda_param
        self.query = query
        self.detected_intent = "BALANCED"  # Will be updated by intent detection

        # Intent-based λ adjustment
        self.lambda_param = self._detect_intent_and_adjust_lambda(query)

        logger.info(f"MMR initialized with λ={self.lambda_param:.2f}")

    def _detect_intent_and_adjust_lambda(self, query: str) -> float:
        """
        Detect query intent and adjust λ accordingly.

        Intent types:
        - SPECIFIC: User wants precise results (higher λ, less diversity)
        - EXPLORATORY: User wants diverse results (lower λ, more diversity)
        - BALANCED: Default mixed intent

        Args:
            query: User's search query

        Returns:
            Adjusted lambda value
        """
        if not query:
            return self.lambda_param

        query_lower = query.lower()

        # SPECIFIC intent indicators (prioritize relevance)
        specific_indicators = [
            'how to', '如何', '怎麼', '怎么',  # How-to queries
            'what is', '什麼是', '什么是',     # Definition queries
            'where', '哪裡', '哪里',           # Location queries
            'when', '什麼時候', '什么时候',    # Time queries
        ]

        # EXPLORATORY intent indicators (prioritize diversity)
        exploratory_indicators = [
            'best', '最好', '推薦', '推荐',    # Recommendation queries
            'ideas', '點子', '想法',           # Brainstorming
            'options', '選項', '选项',         # Comparison shopping
            'alternatives', '替代', '其他',    # Alternative seeking
            'trends', '趨勢', '趋势',          # Trend exploration
            'popular', '熱門', '热门',         # Popularity queries
            'methods', 'ways', '方法', '方式', # Method/approach queries
        ]

        specific_score = sum(1 for indicator in specific_indicators if indicator in query_lower)
        exploratory_score = sum(1 for indicator in exploratory_indicators if indicator in query_lower)

        # Adjust lambda based on intent
        if specific_score > exploratory_score:
            # SPECIFIC: Higher λ (0.8) - prioritize relevance
            adjusted_lambda = 0.8
            self.detected_intent = "SPECIFIC"
            logger.info(f"[INTENT] SPECIFIC query detected - λ adjusted to {adjusted_lambda}")
        elif exploratory_score > specific_score:
            # EXPLORATORY: Lower λ (0.5) - prioritize diversity
            adjusted_lambda = 0.5
            self.detected_intent = "EXPLORATORY"
            logger.info(f"[INTENT] EXPLORATORY query detected - λ adjusted to {adjusted_lambda}")
        else:
            # BALANCED: Use default λ
            adjusted_lambda = self.lambda_param
            self.detected_intent = "BALANCED"
            logger.info(f"[INTENT] BALANCED query - λ remains {adjusted_lambda}")

        return adjusted_lambda

    def _log_diversity_metrics(self, avg_orig_sim: float, avg_mmr_sim: float, diversity_reduction: float) -> None:
        """
        Log diversity improvement metrics via structured logger.

        Args:
            avg_orig_sim: Average similarity before MMR
            avg_mmr_sim: Average similarity after MMR
            diversity_reduction: Improvement (orig - mmr)
        """
        logger.info(
            f"[MMR Metrics] Query: {self.query[:50]} | "
            f"Intent: {self.detected_intent} | "
            f"λ: {self.lambda_param:.2f} | "
            f"Similarity: {avg_orig_sim:.3f} → {avg_mmr_sim:.3f} | "
            f"Reduction: {diversity_reduction:.3f}"
        )

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Cosine similarity score (0 to 1)
        """
        try:
            if len(vec1) != len(vec2):
                logger.warning(f"Dimension mismatch in cosine similarity: {len(vec1)} vs {len(vec2)}")
                return 0.0

            # Convert to numpy arrays
            v1 = np.array(vec1)
            v2 = np.array(vec2)

            # Calculate cosine similarity
            dot_product = np.dot(v1, v2)
            norm_v1 = np.linalg.norm(v1)
            norm_v2 = np.linalg.norm(v2)

            if norm_v1 == 0 or norm_v2 == 0:
                return 0.0

            similarity = dot_product / (norm_v1 * norm_v2)

            # Clamp to [0, 1] range (cosine can be -1 to 1, but for embeddings it's typically 0-1)
            return max(0.0, min(1.0, float(similarity)))

        except Exception as e:
            logger.error(f"Error calculating cosine similarity: {e}")
            return 0.0

    def _precompute_similarity_matrix(self, embeddings: List[List[float]]) -> np.ndarray:
        """
        Pre-compute pairwise cosine similarity matrix for all embeddings.

        This avoids O(k^2 * n * d) repeated cosine similarity calculations
        in the MMR selection loop by computing all pairs once as a matrix operation.

        Args:
            embeddings: List of embedding vectors

        Returns:
            numpy array of shape (n, n) with pairwise cosine similarities
        """
        if not embeddings:
            return np.array([])

        matrix = np.array(embeddings)
        # Normalize rows
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # avoid division by zero
        normalized = matrix / norms
        # Cosine similarity matrix via dot product of normalized vectors
        sim_matrix = normalized @ normalized.T
        # Clamp to [0, 1] range (embeddings typically have non-negative similarity)
        np.clip(sim_matrix, 0.0, 1.0, out=sim_matrix)
        return sim_matrix

    def rerank(self,
               ranked_results: List[Dict[str, Any]],
               query_vector: Optional[List[float]] = None,
               top_k: int = 10) -> Tuple[List[Dict[str, Any]], List[float]]:
        """
        Apply MMR re-ranking to diversify results.

        Args:
            ranked_results: List of ranked documents with 'ranking' scores and 'vector' embeddings
            query_vector: Optional query embedding (not used in current implementation)
            top_k: Number of results to return

        Returns:
            Tuple of (reranked_results, mmr_scores)
        """
        if not ranked_results:
            logger.warning("No results to rerank")
            return [], []

        # Filter results that have vectors
        candidates = [r for r in ranked_results if 'vector' in r and r['vector'] is not None]

        if len(candidates) == 0:
            logger.warning("No results with vectors available for MMR")
            return ranked_results[:top_k], [0.0] * min(top_k, len(ranked_results))

        if len(candidates) <= 3:
            logger.info(f"Only {len(candidates)} results with vectors, skipping MMR")
            return ranked_results[:top_k], [0.0] * min(top_k, len(ranked_results))

        logger.info(f"Applying MMR to {len(candidates)} results")

        # Pre-compute pairwise cosine similarity matrix for all candidates
        embeddings = [c['vector'] for c in candidates]
        sim_matrix = self._precompute_similarity_matrix(embeddings)

        # Normalize ranking scores to [0, 1] for MMR calculation
        scores = [r['ranking'].get('score', 0) for r in candidates]
        max_score = max(scores) if scores else 1.0
        min_score = min(scores) if scores else 0.0
        score_range = max_score - min_score if max_score != min_score else 1.0

        # Initialize
        selected_results = []
        selected_indices = set()
        selected_indices_list = []  # ordered list for matrix lookup
        mmr_scores = []

        # Select first result (highest relevance score)
        first_idx = 0
        selected_results.append(candidates[first_idx])
        selected_indices.add(first_idx)
        selected_indices_list.append(first_idx)

        # Normalized relevance score for first item
        normalized_relevance = (candidates[first_idx]['ranking'].get('score', 0) - min_score) / score_range
        mmr_scores.append(normalized_relevance)

        logger.debug(f"[MMR] Selected 1st: {candidates[first_idx]['name'][:50]} (score={candidates[first_idx]['ranking'].get('score', 0):.1f})")

        # Iteratively select remaining results
        for iteration in range(1, min(top_k, len(candidates))):
            best_mmr_score = -float('inf')
            best_idx = None

            # Evaluate each unselected candidate
            for idx in range(len(candidates)):
                if idx in selected_indices:
                    continue

                # Calculate relevance score (normalized)
                relevance = (candidates[idx]['ranking'].get('score', 0) - min_score) / score_range

                # Calculate max similarity to already-selected documents using pre-computed matrix
                max_similarity = 0.0
                for sel_idx in selected_indices_list:
                    similarity = float(sim_matrix[idx, sel_idx])
                    if similarity > max_similarity:
                        max_similarity = similarity

                # MMR formula: lambda * relevance - (1-lambda) * max_similarity
                mmr_score = self.lambda_param * relevance - (1 - self.lambda_param) * max_similarity

                if mmr_score > best_mmr_score:
                    best_mmr_score = mmr_score
                    best_idx = idx

            if best_idx is not None:
                selected_results.append(candidates[best_idx])
                selected_indices.add(best_idx)
                selected_indices_list.append(best_idx)
                mmr_scores.append(best_mmr_score)

                logger.debug(f"[MMR] Selected {iteration + 1}th: {candidates[best_idx]['name'][:50]} "
                           f"(mmr={best_mmr_score:.3f}, score={candidates[best_idx]['ranking'].get('score', 0):.1f})")

        # Log diversity improvement using pre-computed matrix
        if len(selected_results) >= 2:
            # Calculate average similarity before MMR (top-k results)
            orig_k = min(top_k, len(candidates))
            original_similarities = []
            for i in range(orig_k):
                for j in range(i + 1, orig_k):
                    original_similarities.append(float(sim_matrix[i, j]))

            # Calculate average similarity after MMR using selected indices
            mmr_similarities = []
            for i_pos in range(len(selected_indices_list)):
                for j_pos in range(i_pos + 1, len(selected_indices_list)):
                    idx_i = selected_indices_list[i_pos]
                    idx_j = selected_indices_list[j_pos]
                    mmr_similarities.append(float(sim_matrix[idx_i, idx_j]))

            avg_orig_sim = np.mean(original_similarities) if original_similarities else 0.0
            avg_mmr_sim = np.mean(mmr_similarities) if mmr_similarities else 0.0
            diversity_reduction = avg_orig_sim - avg_mmr_sim

            logger.info(f"[MMR] Diversity improvement: avg similarity {avg_orig_sim:.3f} → {avg_mmr_sim:.3f} "
                       f"(reduction: {diversity_reduction:.3f})")

            # Log diversity metrics to algo/mmr_metrics.log
            self._log_diversity_metrics(avg_orig_sim, avg_mmr_sim, diversity_reduction)

        # Fill remaining slots with non-vector results if needed
        non_vector_results = [r for r in ranked_results if 'vector' not in r or r['vector'] is None]
        remaining_count = top_k - len(selected_results)
        if remaining_count > 0 and non_vector_results:
            selected_results.extend(non_vector_results[:remaining_count])
            mmr_scores.extend([0.0] * min(remaining_count, len(non_vector_results)))

        return selected_results, mmr_scores
