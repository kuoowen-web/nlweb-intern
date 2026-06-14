# XGBoost Ranking Implementation

## Overview

**Purpose**: Replace LLM-based ranking with XGBoost machine learning model to reduce cost by 80%+ and latency by 60%+ while maintaining or improving accuracy through learned user behavior patterns.

**Status**: 🔄 Phase B - Data Collection (waiting for 500+ clicks)

**Timeline**: 9 weeks total
- Phase A: Infrastructure (Week 1-2) - ✅ COMPLETED (shadow mode running in production)
- Phase B: Data Collection (Week 3-6) - IN PROGRESS (waiting for 500+ clicks)
- Phase C: Training & Deployment (Week 7-9) - Three progressive models

---

## Architecture Design

### Complete Ranking Pipeline (After XGBoost Integration)

```
User Query
    ↓
[1] Retrieval (Qdrant Hybrid Search) ✅ COMPLETED
    - Vector similarity (embeddings)
    - BM25 keyword matching
    - Keyword boosting
    - Temporal boosting
    ↓
[2] LLM Ranking (50 results → scored) ✅ EXISTING
    - Semantic relevance scoring
    - Generates ranking scores for all retrieved docs
    ↓
[3] XGBoost Re-ranking (50 results → 50 results) 🔄 NEW - Phase A
    - Feature extraction from LLM scores + retrieval scores
    - ML-based relevance prediction
    - Confidence-based cascading (high confidence → skip LLM fallback)
    ↓
[4] MMR Diversity Re-ranking (50 results → 10 results) ✅ COMPLETED
    - Balance relevance vs diversity
    - Intent-based λ tuning
    ↓
Final 10 Results
```

### Key Design Decisions

**1. XGBoost Position: After LLM, Before MMR**
- **Rationale**:
  - XGBoost uses LLM scores as features (needs LLM to run first)
  - MMR should work on final relevance ranking (needs XGBoost output)
  - This allows graceful degradation (XGBoost disabled → LLM → MMR still works)

**2. Phased Model Evolution**
- **Phase 1** (500-2K clicks): Binary Classification (clicked vs not-clicked)
- **Phase 2** (2K-5K clicks): LambdaMART (pairwise ranking)
- **Phase 3** (5K-10K clicks): XGBRanker (listwise ranking)

**3. Confidence-Based Cascading**
- High confidence (>0.8): Trust XGBoost, skip expensive LLM refinement
- Low confidence (<0.8): Use LLM scores (current behavior)
- Phase A: Always use LLM (XGBoost in shadow mode)

**4. Feature Engineering Strategy**
- Use template-based features that work across all sources
- Combine retrieval signals + LLM signals + document metadata
- Avoid source-specific features (scalability)

---

## Phase A: Infrastructure Preparation (Week 1-2)

### Week 1: Core Module Implementation

#### 1.1 Environment Setup

**File**: `code/python/requirements.txt`

**Changes**:
```txt
# Add ML dependencies (append to end of file)
pandas>=2.0.0
numpy>=1.24.0
scikit-learn>=1.3.0
xgboost>=2.0.0
```

**Rationale**:
- pandas: Data manipulation for feature engineering
- numpy: Numerical operations
- scikit-learn: Train/test split, evaluation metrics
- xgboost: Gradient boosting library

**Installation**:
```bash
pip install pandas numpy scikit-learn xgboost
```

---

#### 1.2 Configuration

**File**: `config/config_retrieval.yaml`

**Add New Section**:
```yaml
xgboost_params:
  enabled: false  # Feature flag (Phase A: disabled, Phase C: enabled)
  model_path: "models/xgboost_ranker_v1_binary.json"  # Will update per phase
  confidence_threshold: 0.8  # High confidence → skip LLM fallback
  feature_version: 2  # Match feature_vectors schema version
  use_shadow_mode: true  # Phase A: log predictions without using them
```

**File**: `code/python/core/config.py`

**Add to CONFIG Class**:
```python
# Around line 50, after bm25_params
self.xgboost_params = retrieval_config.get('xgboost_params', {
    'enabled': False,
    'model_path': 'models/xgboost_ranker_v1_binary.json',
    'confidence_threshold': 0.8,
    'feature_version': 2,
    'use_shadow_mode': True
})
```

---

#### 1.3 Feature Engineering Module

**File**: `code/python/training/feature_engineering.py` (NEW)

**Purpose**: Extract 29 ML features from analytics database and populate `feature_vectors` table.

**Key Functions**:

1. **`populate_feature_vectors(days=30, batch_size=100)`**
   - Reads from 4 analytics tables: queries, retrieved_documents, ranking_scores, user_interactions
   - Extracts 29 features per query-document pair
   - Writes to feature_vectors table in batches
   - Returns: Number of rows inserted

2. **`extract_query_features(query_text)`**
   - Query length, word count, has_quotes, has_numbers
   - Returns: Dict of query-level features

3. **`extract_document_features(doc_title, doc_description, published_date)`**
   - Document length, recency_days, has_author
   - Returns: Dict of document-level features

4. **`extract_query_doc_features(query, doc, bm25_score, vector_score)`**
   - Keyword overlap ratio, exact matches
   - Returns: Dict of interaction features

5. **`extract_ranking_features(retrieval_pos, ranking_pos, scores)`**
   - Position changes, relative scores, percentiles
   - Returns: Dict of ranking features

**Feature List (29 features)**:

See existing analytics schema for full list. Key features:
- Retrieval: vector_similarity, bm25_score, keyword_overlap_ratio
- Document: doc_length, recency_days, schema_completeness
- Ranking: retrieval_position, ranking_position, relative_score
- Labels: clicked (0/1), dwell_time_ms, relevance_grade (0-4)

---

#### 1.4 XGBoost Ranker Module

**File**: `code/python/core/xgboost_ranker.py` (NEW)

**Purpose**: Load trained XGBoost model and perform inference on ranking results.

**Class: XGBoostRanker**

```python
class XGBoostRanker:
    def __init__(self, config):
        """
        Initialize ranker with config.

        Args:
            config: CONFIG.xgboost_params dictionary
        """
        self.enabled = config.get('enabled', False)
        self.model_path = config.get('model_path')
        self.confidence_threshold = config.get('confidence_threshold', 0.8)
        self.feature_version = config.get('feature_version', 2)
        self.use_shadow_mode = config.get('use_shadow_mode', True)
        self.model = None
        self._model_cache = {}  # Global cache to avoid reloading

        if self.enabled:
            self.load_model()

    def load_model(self):
        """Load XGBoost model from disk with caching."""
        # Check global cache first
        # Load JSON model file
        # Store in cache
        pass

    def extract_features(self, ranking_results, query_text):
        """
        Extract 29 features from ranking results.

        Args:
            ranking_results: List of RankingResult objects from ranking.py
            query_text: Original query string

        Returns:
            numpy array of shape (n_results, 29)
        """
        # Extract query features (same for all docs)
        # For each result, extract doc + ranking features
        # Return feature matrix
        pass

    def predict(self, features):
        """
        Predict relevance scores for documents.

        Args:
            features: numpy array (n_results, 29)

        Returns:
            scores: numpy array (n_results,) - predicted relevance 0-1
            confidences: numpy array (n_results,) - prediction confidence 0-1
        """
        # Run model inference
        # Calculate confidence from prediction margin
        # Return scores and confidences
        pass

    def calculate_confidence(self, model_output):
        """
        Calculate prediction confidence from model margins.

        High margin = confident prediction
        Low margin = uncertain prediction

        Args:
            model_output: Raw XGBoost predictions

        Returns:
            numpy array of confidence scores (0-1)
        """
        # Use prediction margin or tree agreement
        pass

    def rerank(self, ranking_results, query_text):
        """
        Re-rank results using XGBoost model.

        Args:
            ranking_results: List from LLM ranking
            query_text: Query string

        Returns:
            reranked_results: List sorted by XGBoost scores
            metadata: Dict with avg_confidence, used_ml, etc.
        """
        if not self.enabled or self.model is None:
            return ranking_results, {'used_ml': False}

        # Extract features
        # Predict scores
        # Calculate confidences

        if self.use_shadow_mode:
            # Log predictions but don't change ranking
            return ranking_results, {
                'used_ml': False,
                'shadow_mode': True,
                'avg_xgboost_score': float(np.mean(scores)),
                'avg_confidence': float(np.mean(confidences))
            }

        # Re-rank by XGBoost scores
        # Attach scores to results
        # Return sorted list
        pass
```

**Key Design Choices**:
- Global model cache to avoid reloading on every query
- Shadow mode for Phase A validation
- Confidence calculation for cascading logic
- Feature extraction matches analytics schema exactly

---

#### 1.5 Training Pipeline

**File**: `code/python/training/xgboost_trainer.py` (NEW)

**Purpose**: Train XGBoost models from analytics data.

**Functions**:

1. **`load_training_data(days=30, min_clicks=500)`**
   - Query feature_vectors table
   - Filter by schema_version=2
   - Return: X (features), y (labels), query_groups

2. **`train_binary_classifier(X, y, hyperparams)`**
   - Phase 1 model: Predict clicked (0/1)
   - XGBoost binary:logistic objective
   - Returns: Trained model, evaluation metrics

3. **`train_lambdamart(X, y, query_groups, hyperparams)`**
   - Phase 2 model: Pairwise ranking
   - XGBoost rank:pairwise objective
   - Returns: Trained model, NDCG@10

4. **`train_xgbranker(X, y, query_groups, hyperparams)`**
   - Phase 3 model: Listwise ranking
   - XGBoost rank:ndcg objective
   - Returns: Trained model, NDCG@10

5. **`evaluate_model(model, X_test, y_test, query_groups_test)`**
   - Calculate NDCG@10, Precision@10, MAP
   - Returns: Dict of metrics

6. **`save_model(model, output_path, metadata)`**
   - Save as JSON format (portable)
   - Include metadata: training date, hyperparams, metrics
   - Create models/ directory if needed

**Hyperparameters** (configurable):

**Phase 1 - Binary Classification**:
```python
binary_params = {
    'objective': 'binary:logistic',
    'max_depth': 6,
    'learning_rate': 0.1,
    'n_estimators': 100,
    'subsample': 0.8,
    'colsample_bytree': 0.8,
    'random_state': 42
}
```

**Phase 2 - LambdaMART**:
```python
lambdamart_params = {
    'objective': 'rank:pairwise',
    'max_depth': 6,
    'learning_rate': 0.05,
    'n_estimators': 200,
    'subsample': 0.8,
    'colsample_bytree': 0.8,
    'random_state': 42
}
```

**Phase 3 - XGBRanker**:
```python
ranker_params = {
    'objective': 'rank:ndcg',
    'max_depth': 7,
    'learning_rate': 0.05,
    'n_estimators': 300,
    'subsample': 0.8,
    'colsample_bytree': 0.8,
    'random_state': 42
}
```

---

### Week 2: Integration & Testing

#### 2.1 Ranking Pipeline Integration

**File**: `code/python/core/ranking.py`

**Insertion Point**: After LLM ranking, before MMR (around line 486)

**Current Flow**:
```python
# Line 450-480: LLM ranking
ranking_results = await rank_items(...)

# Line 486: MMR diversity re-ranking
if use_mmr:
    ranking_results = apply_mmr(ranking_results, ...)
```

**New Flow**:
```python
# Line 450-480: LLM ranking
ranking_results = await rank_items(...)

# NEW: XGBoost re-ranking (insert before MMR)
if CONFIG.xgboost_params.get('enabled', False):
    from core.xgboost_ranker import XGBoostRanker
    xgboost_ranker = XGBoostRanker(CONFIG.xgboost_params)
    ranking_results, xgb_metadata = xgboost_ranker.rerank(
        ranking_results,
        query_text
    )
    # Log metadata for analytics
    logger.info(f"XGBoost metadata: {xgb_metadata}")

# Line 486: MMR diversity re-ranking
if use_mmr:
    ranking_results = apply_mmr(ranking_results, ...)
```

**Analytics Logging**:

Update `code/python/core/query_logger.py` to log XGBoost scores:

```python
# In log_ranking_scores() method
# Add xgboost_score, xgboost_confidence columns to INSERT
```

**Schema Changes** (if needed):

Check if `ranking_scores` table has these columns:
- `xgboost_score DOUBLE PRECISION`
- `xgboost_confidence DOUBLE PRECISION`

If missing, add migration in `query_logger.py`.

---

#### 2.2 Unit Tests

**File**: `code/python/testing/test_xgboost.py` (NEW)

**Test Coverage**:

1. **Test Feature Extraction**
   - Mock ranking results
   - Verify 29 features extracted
   - Check feature value ranges

2. **Test Model Loading**
   - Mock model file (small XGBoost JSON)
   - Verify cache works
   - Test missing file handling

3. **Test Inference Pipeline**
   - Mock trained model
   - Test prediction output shape
   - Verify confidence calculation

4. **Test Shadow Mode**
   - Verify rankings unchanged in shadow mode
   - Check metadata logged correctly

5. **Test Disabled Mode**
   - Verify no-op when enabled=false
   - Check graceful degradation

**Example Test**:
```python
def test_feature_extraction():
    ranker = XGBoostRanker({'enabled': False})
    mock_results = create_mock_ranking_results()
    features = ranker.extract_features(mock_results, "test query")

    assert features.shape == (len(mock_results), 29)
    assert not np.isnan(features).any()
    assert (features[:, 0] >= 0).all()  # Vector similarity >= 0
```

---

#### 2.3 Mock Training Data

**File**: `code/python/testing/mock_training_data.py` (NEW)

**Purpose**: Generate synthetic training data for testing pipeline without real analytics data.

**Functions**:

1. **`generate_mock_features(n_samples=100, n_features=29)`**
   - Generate random feature matrix
   - Use realistic value ranges (e.g., vector_similarity 0-1)
   - Returns: numpy array

2. **`generate_mock_labels(n_samples=100, label_type='binary')`**
   - Binary: Random 0/1 with 10% click rate
   - Regression: Random relevance grades 0-4
   - Returns: numpy array

3. **`generate_mock_query_groups(n_samples=100, queries_per_group=10)`**
   - Group samples by query (for ranking models)
   - Returns: List of group sizes

4. **`create_mock_model(model_type='binary')`**
   - Train tiny XGBoost model on mock data
   - Save to `models/mock_xgboost_ranker.json`
   - Returns: Model object

**Usage in Tests**:
```python
# In test_xgboost.py
from testing.mock_training_data import create_mock_model

def test_model_inference():
    model = create_mock_model('binary')
    # Test inference...
```

---

#### 2.4 Documentation

**File**: `docs/specs/xgboost-spec.md` (THIS FILE)

**Sections**:
- ✅ Architecture overview
- ✅ Phase A implementation details
- 🔄 Feature definitions (reference analytics schema)
- 🔄 Training procedures (Phase C)
- 🔄 Deployment strategy (Phase C)
- 🔄 Monitoring & rollback (Phase C)

**File**: `docs/archive/algo-reviews/Week4_ML_Enhancements.md`

**Updates Needed**:
- Correct pipeline order: LLM → XGBoost → MMR (not LLM → MMR → XGBoost)
- Update architecture diagram
- Clarify XGBoost uses LLM scores as features

---

## Feature Definitions (29 Features)

**Source**: Analytics database `feature_vectors` table (Schema v2)

### Query Features (6 features)
1. `query_length` - Number of characters
2. `word_count` - Number of words/tokens
3. `has_quotes` - Boolean (0/1)
4. `has_numbers` - Boolean (0/1)
5. `has_question_words` - Boolean (0/1)
6. `keyword_count` - Number of keywords extracted

### Document Features (8 features)
7. `doc_length` - Word count in description
8. `recency_days` - Days since publication
9. `has_author` - Boolean (0/1)
10. `has_publication_date` - Boolean (0/1)
11. `schema_completeness` - % of schema fields populated (0-1)
12. `title_length` - Number of characters in title
13. `description_length` - Number of characters in description
14. `url_length` - Number of characters in URL

### Retrieval Features (7 features)
15. `vector_similarity_score` - Cosine similarity from embeddings (0-1)
16. `bm25_score` - Keyword relevance score
17. `keyword_boost` - Keyword boosting score
18. `temporal_boost` - Recency boosting score ⚠️ **Phase A: Set to 0.0, Phase B: Track separately**
19. `final_retrieval_score` - Combined retrieval score
20. `keyword_overlap_ratio` - Query-doc keyword overlap (0-1)
21. `title_exact_match` - Boolean (0/1)

**⚠️ Implementation Note - temporal_boost**:
- **Phase A Status**: Current Qdrant implementation multiplies `recency_multiplier` directly into `final_score` (qdrant.py:971), no separate `temporal_boost` variable tracked
- **Phase A Workaround**: Set `temporal_boost = 0.0` as placeholder in `xgboost_ranker.py` feature extraction
- **Phase B Implementation**: Modify `qdrant.py` to track `temporal_boost = recency_multiplier - 1.0` separately in `point_scores` dict
- **Rationale**:
  - Recalculating from `published_date` risks formula mismatch (training ≠ inference)
  - Phase A dummy model doesn't need accurate temporal_boost
  - Phase B data collection requires correct values for model training
- **Related**: Issue #3.1 Modification #2 - Qdrant return format change

### Ranking Features (6 features)
22. `retrieval_position` - Position in retrieval results (0-based)
23. `ranking_position` - Position after LLM ranking (0-based)
24. `llm_final_score` - LLM-assigned relevance score
25. `relative_score_to_top` - Score normalized by top result (0-1)
26. `score_percentile` - Percentile ranking in result set (0-100)
27. `position_change` - retrieval_position - ranking_position

### MMR Features (2 features)
28. `mmr_diversity_score` - Diversity score from MMR (Phase A: not available, use 0)
29. `detected_intent` - Encoded intent (SPECIFIC=0, EXPLORATORY=1, BALANCED=2)

### Labels (3 labels, not features)
- `clicked` - Boolean (0/1) - PRIMARY LABEL for Phase 1
- `dwell_time_ms` - Engagement time (0-300000)
- `relevance_grade` - Manual grade (0-4) - For Phase 2/3

**Feature Extraction Priority**:

**Phase A**: Must extract all 29 features (use 0 for mmr_diversity_score)
**Phase C Training**: Use clicked as label for Phase 1, relevance_grade for Phase 2/3

---

## Phase B: Data Collection (Week 3-6)

### Goals
- Collect 500+ clicked samples (minimum for Phase 1)
- Target: 10,000+ total queries with interactions
- Verify data quality and feature completeness

### Monitoring

**Check Data Volume**:
```bash
# Via analytics API
curl https://taiwan-news-ai-search.onrender.com/api/analytics/stats?days=30
```

**Check Click Rate**:
```sql
SELECT
    COUNT(DISTINCT query_id) as total_queries,
    COUNT(DISTINCT CASE WHEN clicked THEN query_id END) as queries_with_clicks,
    COUNT(*) FILTER (WHERE clicked) as total_clicks
FROM user_interactions
WHERE timestamp > NOW() - INTERVAL '30 days';
```

**Trigger Points**:
- 500 clicks → Start Phase 1 (Binary Classification)
- 2,000 clicks → Start Phase 2 (LambdaMART)
- 5,000 clicks → Start Phase 3 (XGBRanker)

### Data Quality Checks

Run periodically:
```python
# In feature_engineering.py
def validate_feature_quality(days=30):
    """Check for missing values, outliers, data drift."""
    # Query feature_vectors table
    # Check % of NULL values per feature
    # Check value ranges (detect outliers)
    # Return data quality report
```

**Action Items**:
- If >10% NULL values in any feature → Investigate analytics logging
- If unusual distributions → Check for bugs in feature extraction
- If click rate <1% → May need more traffic or better ranking

---

## Phase C: Training & Deployment (Week 7-9)

### Phase 1: Binary Classification POC (500-2K clicks)

**Timeline**: 3-5 days after reaching 500 clicks

**Steps**:

1. **Feature Extraction** (1 day)
   ```bash
   python -m training.feature_engineering --days 30
   ```
   - Populates feature_vectors table
   - Verify 500+ rows with clicked=1

2. **Model Training** (1 day)
   ```bash
   python -m training.xgboost_trainer --model_type binary --output models/xgboost_ranker_v1_binary.json
   ```
   - Train on all available data (500-2K samples)
   - Train/test split: 80/20
   - Target: AUC > 0.65 (better than random)

3. **Evaluation** (1 day)
   - Precision@10, Recall@10
   - Feature importance analysis
   - Compare against LLM baseline

4. **Shadow Mode Deployment** (1-2 days)
   ```yaml
   # config/config_retrieval.yaml
   xgboost_params:
     enabled: true
     use_shadow_mode: true
     model_path: "models/xgboost_ranker_v1_binary.json"
   ```
   - XGBoost runs but doesn't affect rankings
   - Log predictions to analytics
   - Monitor for 1-2 days

5. **Production Deployment** (if shadow mode successful)
   ```yaml
   xgboost_params:
     enabled: true
     use_shadow_mode: false  # Use XGBoost scores
   ```
   - Monitor CTR, latency, cost
   - Rollback if degradation

**Success Criteria**:
- AUC > 0.65
- No latency increase (inference <100ms)
- CTR unchanged or improved

---

### Phase 2: LambdaMART Optimization (2K-5K clicks)

**Timeline**: 1 week after reaching 2K clicks

**Improvements over Phase 1**:
- Pairwise ranking objective (better than binary classification)
- More training data (2K-5K clicks)
- Target: NDCG@10 > 0.65

**Steps**: Same as Phase 1, but use `--model_type lambdamart`

**Output**: `models/xgboost_ranker_v2_lambdamart.json`

**A/B Testing**:
- 10% traffic: v2 LambdaMART model
- 90% traffic: v1 Binary model
- Compare NDCG, CTR, cost
- Gradual rollout: 10% → 50% → 100%

---

### Phase 3: XGBRanker Production (5K-10K clicks)

**Timeline**: 1-2 weeks after reaching 5K clicks

**Improvements over Phase 2**:
- Listwise ranking objective (optimal for NDCG)
- Large training dataset (5K-10K clicks)
- Target: NDCG@10 > 0.70

**Steps**: Same as Phase 2, but use `--model_type ranker`

**Output**: `models/xgboost_ranker_v3_listwise.json`

**Confidence Threshold Tuning**:
- Current: 0.8 (conservative)
- Goal: Lower to 0.75 (fewer LLM fallbacks)
- Monitor accuracy degradation (<5% acceptable)

**Expected Impact**:
- 80%+ queries skip LLM refinement
- Cost: $1.20 → $0.15 per query (88% reduction)
- Latency: 20s → 5s (75% reduction)

---

## Monitoring & Analytics

### Key Metrics to Track

**Model Performance**:
- NDCG@10, Precision@10, MAP
- Inference latency (P50, P95, P99)
- Model confidence distribution

**Business Metrics**:
- Click-through rate (CTR)
- Average dwell time
- Bounce rate
- User satisfaction

**System Metrics**:
- Cost per query
- Total query latency
- Error rate

**Feature Drift**:
- Monitor feature distributions monthly
- Alert if feature means shift >20%
- Trigger retraining if drift detected

### Logging Strategy

**Shadow Mode** (Phase A, early Phase C):
```python
logger.info(f"[XGBoost Shadow] Query: {query_id}, Avg Score: {avg_score:.3f}, Avg Confidence: {avg_conf:.3f}")
```

**Production Mode** (late Phase C):
```python
logger.info(f"[XGBoost] Used ML: {used_ml}, Avg Confidence: {avg_conf:.3f}, LLM Fallback: {llm_fallback_count}")
```

**Analytics Database**:
- Log xgboost_score, xgboost_confidence to ranking_scores table
- Track model version used per query

---

## Rollback Procedures

### Emergency Rollback (Production Issues)

**Symptoms**:
- CTR drops >10%
- Latency increases >50%
- Error rate spikes

**Action** (5 minutes):
```yaml
# config/config_retrieval.yaml
xgboost_params:
  enabled: false  # Instant disable
```

Restart service. System reverts to LLM → MMR pipeline.

### Partial Rollback (Quality Issues)

**Symptoms**:
- Confidence scores lower than expected
- Specific query types performing poorly

**Action** (10 minutes):
```yaml
xgboost_params:
  enabled: true
  use_shadow_mode: true  # Back to shadow mode
```

Investigate predictions, retrain model, re-deploy.

### Model Version Rollback

**Symptoms**:
- v3 model underperforming v2

**Action**:
```yaml
xgboost_params:
  model_path: "models/xgboost_ranker_v2_lambdamart.json"  # Revert to v2
```

No code changes needed, just config update.

---

## File Structure Summary

### New Files (Phase A)

```
NLWeb/
├── algo/
│   └── XGBoost_implementation.md          # This file
├── code/python/
│   ├── core/
│   │   └── xgboost_ranker.py              # Inference module
│   ├── training/                           # New directory
│   │   ├── __init__.py
│   │   ├── feature_engineering.py         # Feature extraction
│   │   └── xgboost_trainer.py             # Training pipeline
│   └── testing/
│       ├── test_xgboost.py                # Unit tests
│       └── mock_training_data.py          # Test data generator
├── models/                                 # New directory
│   ├── xgboost_ranker_v1_binary.json      # Phase 1 model (Phase C)
│   ├── xgboost_ranker_v2_lambdamart.json  # Phase 2 model (Phase C)
│   ├── xgboost_ranker_v3_listwise.json    # Phase 3 model (Phase C)
│   ├── feature_config_v2.json             # Feature metadata
│   └── mock_xgboost_ranker.json           # Test model
└── config/
    └── config_retrieval.yaml              # Updated with xgboost_params
```

### Modified Files (Phase A)

```
code/python/
├── requirements.txt                        # Add pandas, numpy, sklearn, xgboost
├── core/
│   ├── config.py                          # Add xgboost_params to CONFIG
│   ├── ranking.py                         # Insert XGBoost call before MMR
│   └── query_logger.py                    # Log xgboost_score, xgboost_confidence
└── testing/
    └── (existing test files)              # May need updates
```

---

## Risk Mitigation

### High Risk

**1. Cache Not Synchronized with XGBoost**
- **Risk**: Generate mode uses cached results, XGBoost not re-executed
- **Mitigation** (Phase A): Document this in Week 2, handle in integration
- **Resolution** (Phase C): Re-run XGBoost inference even on cache hits

**2. MMR Overrides XGBoost Rankings**
- **Risk**: XGBoost optimizes ranking, MMR shuffles for diversity, breaks optimization
- **Mitigation** (Phase A): XGBoost BEFORE MMR (correct order)
- **Validation** (Phase C): Compare NDCG with/without MMR

### Medium Risk

**1. Insufficient Training Data**
- **Risk**: <500 clicks → Model won't converge
- **Mitigation**: Phased approach (Binary → LambdaMART → Ranker)
- **Fallback**: Stay in Phase 1 longer if needed

**2. Model Loading Latency**
- **Risk**: Loading model on every query adds latency
- **Mitigation**: Global model cache (load once, reuse)
- **Validation**: Profile inference time (<100ms target)

### Low Risk

**1. Feature Version Mismatch**
- **Risk**: Schema v2 features, model trained on v1
- **Mitigation**: Only enable XGBoost when schema_version=2
- **Check**: Validate feature_version in config matches schema

**2. Score Range Inconsistency**
- **Risk**: XGBoost scores 0-1, LLM scores 0-100, MMR confused
- **Mitigation**: Normalize XGBoost scores to 0-100 range
- **Validation**: Unit tests verify score ranges

---

## Success Metrics

### Phase A (Infrastructure) - Week 2 Completion

- ✅ All modules implemented and importable
- ✅ Unit tests passing (>80% coverage)
- ✅ Mock model can run inference
- ✅ Integration point identified in ranking.py
- ✅ Documentation complete

### Phase C1 (Binary Model) - After 500 Clicks

- AUC > 0.65
- Inference latency <100ms (P95)
- Shadow mode: No production impact
- Production mode: CTR unchanged or +5%

### Phase C2 (LambdaMART) - After 2K Clicks

- NDCG@10 > 0.65
- Cost reduction: 40-50% vs LLM-only baseline
- Latency reduction: 30-40% vs LLM-only baseline

### Phase C3 (XGBRanker) - After 5K Clicks

- NDCG@10 > 0.70
- Cost reduction: 80%+ (target: $1.20 → $0.15 per query)
- Latency reduction: 60%+ (target: 20s → 5s)
- 80%+ queries use XGBoost (high confidence)

---

## Changelog

### 2025-01-26 (Phase A Start)
- Created XGBoost_implementation.md
- Defined architecture: LLM → XGBoost → MMR pipeline
- Specified 29 features from analytics schema
- Planned phased training approach (Binary → LambdaMART → Ranker)
- Outlined Week 1-2 implementation tasks

### Future Updates
- Phase A completion: Note implementation status, test results
- Phase B: Track data accumulation progress
- Phase C: Document training results, A/B test outcomes, production metrics

---

## References

- **Analytics Schema**: `.claude/CLAUDE.md` (Analytics Database Schema section)
- **BM25 Implementation**: `docs/specs/bm25-spec.md`
- **MMR Implementation**: `docs/specs/mmr-spec.md`
- **Future ML Plans**: `docs/archive/algo-reviews/Week4_ML_Enhancements.md`
- **XGBoost Documentation**: https://xgboost.readthedocs.io/
- **LambdaMART Paper**: Burges (2010) - "From RankNet to LambdaRank to LambdaMART"
