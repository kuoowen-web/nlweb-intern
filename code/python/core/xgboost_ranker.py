# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
XGBoost Ranker Module for ML-based Ranking

Loads trained XGBoost models and performs inference on ranking results.
Supports shadow mode for validation and confidence-based cascading.

WARNING: This code is under development and may undergo changes in future releases.
Backwards compatibility is not guaranteed at this time.
"""

import os
import json
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from misc.logger.logging_config_helper import get_configured_logger

# Import feature index constants
from training.feature_engineering import (
    FEATURE_IDX_LLM_FINAL_SCORE,
    TOTAL_FEATURES_PHASE_A
)

logger = get_configured_logger("xgboost_ranker")

# Global model cache to avoid reloading on every query
_MODEL_CACHE: Dict[str, Any] = {}


class XGBoostRanker:
    """
    XGBoost-based ranking model for ML-driven result re-ranking.

    This class loads trained XGBoost models and performs fast inference
    on ranking results. It supports:
    - Shadow mode (log predictions without affecting rankings)
    - Confidence-based cascading (high confidence → trust ML)
    - Global model caching (avoid reload latency)
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize XGBoost ranker with configuration.

        Args:
            config: XGBoost configuration dictionary from CONFIG.xgboost_params
                - enabled (bool): Feature flag
                - model_path (str): Path to trained model JSON
                - confidence_threshold (float): Confidence threshold (0-1)
                - feature_version (int): Expected feature version
                - use_shadow_mode (bool): Shadow mode flag
        """
        self.enabled = config.get('enabled', False)
        self.model_path = config.get('model_path', 'models/xgboost_ranker_v1_binary.json')
        self.confidence_threshold = config.get('confidence_threshold', 0.8)
        self.feature_version = config.get('feature_version', 2)
        self.use_shadow_mode = config.get('use_shadow_mode', True)
        self.model = None

        # Load model if enabled
        if self.enabled:
            self.load_model()
        else:
            logger.info("XGBoost ranker disabled (enabled=false in config)")

    def load_model(self) -> None:
        """
        Load XGBoost model from disk with global caching.

        Uses global cache to avoid reloading the same model multiple times.
        Model is loaded from JSON format for portability.

        Raises:
            FileNotFoundError: If model file doesn't exist
            ValueError: If model loading fails
        """
        # Check global cache first
        if self.model_path in _MODEL_CACHE:
            self.model = _MODEL_CACHE[self.model_path]
            logger.info(f"Loaded XGBoost model from cache: {self.model_path}")
            return

        # Check if model file exists
        if not os.path.exists(self.model_path):
            logger.warning(f"XGBoost model not found: {self.model_path}")
            if self.use_shadow_mode:
                logger.info("Phase A: Shadow mode active - will use dummy predictions")
                # Keep self.enabled = True, self.model = None
                # predict() will use dummy predictions based on LLM scores
            else:
                logger.warning("Model will be trained in Phase C. Disabling XGBoost.")
                self.enabled = False
            return

        try:
            # Phase A: Placeholder for model loading
            # Phase C: Load actual XGBoost model
            # import xgboost as xgb
            # self.model = xgb.Booster()
            # self.model.load_model(self.model_path)

            logger.warning(f"XGBoost model loading not yet implemented (Phase A)")
            logger.info(f"Model path configured: {self.model_path}")

            # Store in global cache
            _MODEL_CACHE[self.model_path] = self.model

        except Exception as e:
            logger.error(f"Failed to load XGBoost model: {e}")
            self.enabled = False

    def extract_features(self, ranking_results: List[Any], query_text: str) -> np.ndarray:
        """
        Extract 29 features from in-memory ranking results.

        Args:
            ranking_results: List of ranking result objects (from ranking.py)
            query_text: Original query string

        Returns:
            numpy array of shape (n_results, 29) with extracted features

        Features (in order):
            Query (6): query_length, word_count, has_quotes, has_numbers,
                       has_question_words, keyword_count
            Document (8): doc_length, recency_days, has_author, has_publication_date,
                         schema_completeness, title_length, description_length, url_length
            Retrieval (7): vector_similarity, bm25_score, keyword_boost, temporal_boost,
                          final_retrieval_score, keyword_overlap_ratio, title_exact_match
            Ranking (6): retrieval_position, ranking_position, llm_final_score,
                        relative_score_to_top, score_percentile, position_change
            MMR (2): mmr_diversity_score, detected_intent
        """
        # Import feature extraction functions
        from training.feature_engineering import (
            extract_query_features,
            extract_document_features,
            extract_query_doc_features,
            extract_ranking_features,
            extract_mmr_features
        )

        n_results = len(ranking_results)
        features = np.zeros((n_results, 29))

        # Extract query features (same for all documents)
        query_feats = extract_query_features(query_text)

        # Collect all LLM scores for percentile calculation
        all_llm_scores = []
        for result in ranking_results:
            llm_score = result.get('ranking', {}).get('score', 0.0)
            all_llm_scores.append(llm_score)

        # Extract features for each result
        for i, result in enumerate(ranking_results):
            doc_title = result.get('name', '')  # 'name' is the title field
            doc_url = result.get('url', '')
            schema_object = result.get('schema_object', {})
            doc_description = schema_object.get('description', '')
            published_date = schema_object.get('datePublished')
            author = schema_object.get('author')

            # Retrieval scores from nested dict
            retrieval_scores = result.get('retrieval_scores', {})
            vector_score = retrieval_scores.get('vector_score', 0.0)
            bm25_score = retrieval_scores.get('bm25_score', 0.0)
            keyword_boost = retrieval_scores.get('keyword_boost', 0.0)
            temporal_boost = retrieval_scores.get('temporal_boost', 0.0)
            final_retrieval_score = retrieval_scores.get('final_retrieval_score', 0.0)

            # Ranking scores
            retrieval_position = i  # Phase A: we don't track original retrieval position
            ranking_position = i  # Current position after LLM ranking
            llm_score = result.get('ranking', {}).get('score', 0.0)

            # MMR scores (not available at this point in the pipeline)
            mmr_score = None
            detected_intent = 'BALANCED'  # Default intent

            # Extract document features
            doc_feats = extract_document_features(
                doc_title, doc_description, published_date, author, doc_url
            )

            # Extract query-doc features
            query_doc_feats = extract_query_doc_features(
                query_text, doc_title, doc_description,
                bm25_score, vector_score, keyword_boost,
                temporal_boost, final_retrieval_score
            )

            # Extract ranking features
            ranking_feats = extract_ranking_features(
                retrieval_position, ranking_position,
                llm_score, all_llm_scores
            )

            # Extract MMR features
            mmr_feats = extract_mmr_features(mmr_score, detected_intent)

            # Combine all features (29 total)
            feature_vector = [
                # Query features (6)
                query_feats['query_length'],
                query_feats['word_count'],
                query_feats['has_quotes'],
                query_feats['has_numbers'],
                query_feats['has_question_words'],
                query_feats['keyword_count'],

                # Document features (8)
                doc_feats['doc_length'],
                doc_feats['recency_days'],
                doc_feats['has_author'],
                doc_feats['has_publication_date'],
                doc_feats['schema_completeness'],
                doc_feats['title_length'],
                doc_feats['description_length'],
                doc_feats['url_length'],

                # Retrieval features (7)
                query_doc_feats['vector_similarity_score'],
                query_doc_feats['bm25_score'],
                query_doc_feats['keyword_boost'],
                query_doc_feats['temporal_boost'],
                query_doc_feats['final_retrieval_score'],
                query_doc_feats['keyword_overlap_ratio'],
                query_doc_feats['title_exact_match'],

                # Ranking features (6)
                ranking_feats['retrieval_position'],
                ranking_feats['ranking_position'],
                ranking_feats['llm_final_score'],
                ranking_feats['relative_score_to_top'],
                ranking_feats['score_percentile'],
                ranking_feats['position_change'],

                # MMR features (2)
                mmr_feats['mmr_diversity_score'],
                mmr_feats['detected_intent']
            ]

            features[i, :] = feature_vector

        return features

    def predict(self, features: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Predict relevance scores and confidences for documents.

        Args:
            features: numpy array (n_results, 29)

        Returns:
            Tuple of:
            - scores: numpy array (n_results,) - predicted relevance 0-1
            - confidences: numpy array (n_results,) - prediction confidence 0-1

        Note: Phase A returns dummy predictions. Phase C uses actual model.
        """
        n_results = features.shape[0]

        # Validate feature count
        assert features.shape[1] == TOTAL_FEATURES_PHASE_A, \
            f"Expected {TOTAL_FEATURES_PHASE_A} features, got {features.shape[1]}"

        if self.model is None:
            # Phase A: Return dummy predictions based on LLM scores
            llm_scores = features[:, FEATURE_IDX_LLM_FINAL_SCORE]

            # Normalize to 0-1 range
            if llm_scores.max() > 0:
                normalized_scores = llm_scores / llm_scores.max()
            else:
                normalized_scores = np.zeros(n_results)

            # Dummy confidences (uniform)
            confidences = np.full(n_results, 0.5)

            return normalized_scores, confidences

        # Phase C: Actual XGBoost prediction
        # import xgboost as xgb
        # dmatrix = xgb.DMatrix(features)
        # predictions = self.model.predict(dmatrix)
        # confidences = self.calculate_confidence(predictions)
        # return predictions, confidences

        # Placeholder for Phase A
        return np.zeros(n_results), np.zeros(n_results)

    def calculate_confidence(self, predictions: np.ndarray) -> np.ndarray:
        """
        Calculate prediction confidence from model output.

        High prediction margin = confident prediction
        Low prediction margin = uncertain prediction

        Args:
            predictions: Raw model predictions

        Returns:
            numpy array of confidence scores (0-1)

        Note: Actual implementation depends on model type:
        - Binary: Use prediction probability (close to 0 or 1 = high confidence)
        - Ranker: Use prediction margin or tree agreement variance
        """
        # Phase A: Dummy confidence
        confidences = np.abs(predictions - 0.5) * 2  # 0.5 → 0, 0 or 1 → 1

        return confidences

    def rerank(
        self,
        ranking_results: List[Any],
        query_text: str
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """
        Re-rank results using XGBoost model.

        Args:
            ranking_results: List of ranking result objects from LLM ranking
            query_text: Original query string

        Returns:
            Tuple of:
            - reranked_results: List sorted by XGBoost scores (or unchanged if disabled/shadow)
            - metadata: Dict with avg_confidence, used_ml, shadow_mode, etc.

        Behavior:
        - If disabled: Return unchanged results
        - If shadow mode: Log predictions but don't change ranking
        - If production: Re-rank by XGBoost scores
        """
        metadata = {
            'used_ml': False,
            'shadow_mode': self.use_shadow_mode,
            'avg_xgboost_score': 0.0,
            'avg_confidence': 0.0,
            'num_results': len(ranking_results)
        }

        # Check if enabled (allow shadow mode even without model in Phase A)
        if not self.enabled:
            logger.debug("XGBoost disabled in config")
            return ranking_results, metadata

        # Phase A: Allow shadow mode even if model is None (uses dummy predictions)
        if self.model is None and not self.use_shadow_mode:
            logger.debug("XGBoost model not loaded and not in shadow mode")
            return ranking_results, metadata

        try:
            # Extract features
            features = self.extract_features(ranking_results, query_text)

            # Predict scores and confidences
            scores, confidences = self.predict(features)

            # Calculate metadata
            avg_score = float(np.mean(scores))
            avg_confidence = float(np.mean(confidences))

            metadata['avg_xgboost_score'] = avg_score
            metadata['avg_confidence'] = avg_confidence

            # Shadow mode: Log predictions to analytics but don't change ranking
            if self.use_shadow_mode:
                # Calculate comparison metrics (Task B2)
                comparison_metrics = self._calculate_comparison_metrics(
                    ranking_results, scores
                )

                logger.info(
                    f"[XGBoost Shadow] Query: {query_text[:50]}..., "
                    f"Avg Score: {avg_score:.3f}, Avg Confidence: {avg_confidence:.3f}, "
                    f"Top10 Overlap: {comparison_metrics['top10_overlap']:.2f}, "
                    f"Rank Corr: {comparison_metrics['rank_correlation']:.3f}"
                )

                # Add comparison metrics to metadata
                metadata.update(comparison_metrics)

                # Log XGBoost predictions to analytics database (Phase A)
                from core.query_logger import get_query_logger
                query_logger = get_query_logger()

                for i, result in enumerate(ranking_results):
                    try:
                        query_id = result.get('query_id')
                        doc_url = result.get('url', '')

                        if query_id:
                            query_logger.log_xgboost_scores(
                                query_id=query_id,
                                doc_url=doc_url,
                                xgboost_score=float(scores[i]),
                                xgboost_confidence=float(confidences[i]),
                                ranking_position=i
                            )
                    except Exception as log_err:
                        logger.warning(f"Failed to log XGBoost score for result {i}: {log_err}")

                logger.debug(f"[XGBoost Shadow] Logged predictions to analytics")
                return ranking_results, metadata

            # Production mode: Re-rank by XGBoost scores
            for i, result in enumerate(ranking_results):
                result['xgboost_score'] = float(scores[i])
                result['xgboost_confidence'] = float(confidences[i])

            # Sort by XGBoost scores (descending)
            reranked_results = sorted(
                ranking_results,
                key=lambda x: x.get('xgboost_score', 0.0),
                reverse=True
            )

            metadata['used_ml'] = True

            logger.info(
                f"[XGBoost] Re-ranked {len(reranked_results)} results, "
                f"Avg Confidence: {avg_confidence:.3f}"
            )

            return reranked_results, metadata

        except Exception as e:
            logger.error(f"XGBoost reranking failed: {e}")
            logger.exception("Full traceback:")
            return ranking_results, metadata



    def _calculate_comparison_metrics(
        self,
        ranking_results: List[Any],
        xgboost_scores: np.ndarray
    ) -> Dict[str, float]:
        """
        Calculate comparison metrics between LLM and XGBoost rankings (Task B2).

        Args:
            ranking_results: List of ranking results (LLM-ranked)
            xgboost_scores: XGBoost predicted scores

        Returns:
            Dict with comparison metrics:
            - top10_overlap: Overlap between top-10 (0-1)
            - rank_correlation: Kendall's Tau correlation
            - avg_position_change: Average position change
        """
        try:
            from scipy.stats import kendalltau
        except ImportError:
            logger.warning("scipy not available, using fallback correlation")
            kendalltau = None

        n = len(ranking_results)

        # Get LLM ranking (already in order)
        llm_urls = [result.get('url', '') for result in ranking_results]

        # Get XGBoost ranking (sort by predicted scores)
        xgb_ranked_indices = np.argsort(-xgboost_scores)  # Descending
        xgb_urls = [llm_urls[i] for i in xgb_ranked_indices]

        # 1. Top-10 overlap
        top_k = min(10, n)
        llm_top10 = set(llm_urls[:top_k])
        xgb_top10 = set(xgb_urls[:top_k])
        overlap = len(llm_top10.intersection(xgb_top10))
        top10_overlap = overlap / top_k if top_k > 0 else 0.0

        # 2. Rank correlation (Kendall's Tau)
        if kendalltau and n > 1:
            # Create rank arrays
            llm_ranks = {url: i for i, url in enumerate(llm_urls)}
            xgb_ranks = {url: i for i, url in enumerate(xgb_urls)}

            # Ensure same order for correlation
            common_urls = [url for url in llm_urls if url in xgb_ranks]
            if len(common_urls) > 1:
                llm_rank_array = [llm_ranks[url] for url in common_urls]
                xgb_rank_array = [xgb_ranks[url] for url in common_urls]
                tau, _ = kendalltau(llm_rank_array, xgb_rank_array)
                from math import isnan
                if isnan(tau):
                    tau = 0.0
                rank_correlation = float(tau)
            else:
                rank_correlation = 0.0
        else:
            # Fallback: Spearman's rho approximation
            if n > 1:
                llm_ranks_array = np.arange(n)
                xgb_ranks_array = np.array([llm_urls.index(url) for url in xgb_urls])
                rank_correlation = float(np.corrcoef(llm_ranks_array, xgb_ranks_array)[0, 1])
            else:
                rank_correlation = 0.0

        # 3. Average position change
        position_changes = []
        for i, url in enumerate(llm_urls):
            if url in xgb_urls:
                xgb_position = xgb_urls.index(url)
                position_changes.append(abs(i - xgb_position))

        avg_position_change = float(np.mean(position_changes)) if position_changes else 0.0

        return {
            'top10_overlap': top10_overlap,
            'rank_correlation': rank_correlation,
            'avg_position_change': avg_position_change
        }


if __name__ == "__main__":
    # Test XGBoost ranker with mock data
    print("Testing XGBoost Ranker Module")
    print("=" * 50)

    # Mock configuration
    config = {
        'enabled': True,
        'model_path': 'models/mock_xgboost_ranker.json',
        'confidence_threshold': 0.8,
        'feature_version': 2,
        'use_shadow_mode': True
    }

    # Create ranker
    ranker = XGBoostRanker(config)
    print(f"Ranker initialized: enabled={ranker.enabled}, shadow_mode={ranker.use_shadow_mode}")

    # Mock ranking results (Dict format matching ranking.py output)
    def _make_mock_result(title, score, position):
        return {
            'url': f"https://example.com/{position}",
            'name': title,
            'site': 'test_site',
            'ranking': {'score': score, 'description': f"Description for {title}"},
            'schema_object': {
                'description': f"Description for {title}",
                'datePublished': "2025-01-20T10:00:00Z",
                'author': "Test Author",
            },
            'retrieval_scores': {
                'vector_score': 0.8,
                'bm25_score': 100.0,
                'keyword_boost': 5.0,
                'temporal_boost': 2.0,
                'final_retrieval_score': 107.0,
            },
        }

    mock_results = [
        _make_mock_result("Result 1", 95.0, 0),
        _make_mock_result("Result 2", 90.0, 1),
        _make_mock_result("Result 3", 85.0, 2),
        _make_mock_result("Result 4", 80.0, 3),
        _make_mock_result("Result 5", 75.0, 4),
    ]

    query = "Test query for XGBoost"

    # Test feature extraction
    print(f"\nExtracting features for {len(mock_results)} results...")
    features = ranker.extract_features(mock_results, query)
    print(f"Feature shape: {features.shape}")
    print(f"Expected: ({len(mock_results)}, 29)")
    print(f"Feature extraction: {'PASS' if features.shape == (5, 29) else 'FAIL'}")

    # Test reranking
    print(f"\nTesting reranking...")
    reranked, metadata = ranker.rerank(mock_results, query)
    print(f"Metadata: {metadata}")
    print(f"Shadow mode: {metadata['shadow_mode']}")
    print(f"Used ML: {metadata['used_ml']}")
    print(f"Avg XGBoost Score: {metadata['avg_xgboost_score']:.3f}")
    print(f"Avg Confidence: {metadata['avg_confidence']:.3f}")

    print(f"\n{'=' * 50}")
    print("XGBoost Ranker Module Test Complete")
