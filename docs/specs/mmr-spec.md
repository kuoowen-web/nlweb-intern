# MMR (Maximal Marginal Relevance) Implementation

## Overview

**Purpose**: Replace LLM-based diversity re-ranking with an algorithmic approach using MMR to balance relevance and diversity in search results.

**Status**: ✅ Implemented (Week 1-2, Track C)

**Expected Impact**:
- 40% cost reduction (combined with BM25, Week 3 target)
- 40% latency reduction (eliminates LLM diversity prompt)
- More consistent diversity (algorithmic vs LLM-based)

---

## Algorithm Description

### Core Formula

```
MMR = λ * Relevance(doc, query) - (1-λ) * max(Similarity(doc, selected_docs))
```

Where:
- **λ (lambda)**: Trade-off parameter between relevance and diversity
  - λ = 1.0: Pure relevance ranking (no diversity)
  - λ = 0.7: Default balanced (70% relevance, 30% diversity)
  - λ = 0.5: Equal weight
  - λ = 0.0: Pure diversity (maximum novelty)

- **Relevance**: Document's LLM ranking score (normalized to [0, 1])
- **Similarity**: Cosine similarity between document embeddings

### Iterative Selection

1. **Initialize**: Select highest-ranked document (by LLM score)
2. **Iterate**: For each remaining slot:
   - Calculate MMR score for all unselected documents
   - Select document with highest MMR score
   - Add to selected set
3. **Repeat**: Until top-k documents selected

This greedy algorithm ensures each new document maximizes relevance while minimizing similarity to already-selected documents.

---

## Intent-Based Lambda Tuning

MMR automatically adjusts λ based on detected query intent:

### SPECIFIC Intent (λ = 0.8)
**Prioritizes relevance** - user wants precise answers

Indicators:
- How-to queries: "how to", "如何", "怎麼"
- Definitions: "what is", "什麼是"
- Location: "where", "哪裡"
- Time: "when", "什麼時候"

Example: "How to make sourdough bread" → λ=0.8 (focus on best recipes, less diversity)

### EXPLORATORY Intent (λ = 0.5)
**Prioritizes diversity** - user wants options/exploration

Indicators:
- Recommendations: "best", "最好", "推薦"
- Brainstorming: "ideas", "點子"
- Comparison: "options", "選項", "alternatives"
- Trends: "trends", "趨勢", "popular"

Example: "Best kitchen gadgets 2025" → λ=0.5 (show diverse product types)

### BALANCED Intent (λ = 0.7)
**Default** - mixed or unclear intent

---

## Implementation Details

### File Structure

| File | Purpose |
|------|---------|
| `code/python/core/mmr.py` | MMR algorithm implementation |
| `code/python/core/ranking.py` | Integration into ranking pipeline |
| `code/python/retrieval_providers/postgres_client.py` | Vector retrieval with `include_vectors=True` (PG) |
| `code/python/core/baseHandler.py` | Requests vectors when MMR enabled |
| `config/config_retrieval.yaml` | MMR configuration parameters |

### Data Flow

```
1. User Query
   ↓
2. Retrieval (postgres_client.py)
   - Vector search with include_vectors=True (if MMR enabled)
   - Returns: [url, schema_json, name, site, vector]
   ↓
3. Ranking (ranking.py:370-520)
   - LLM scores each document
   - Temporal boosting (if applicable)
   - Filter by score > 51
   - Sort by score (descending)
   ↓
4. MMR Re-ranking (ranking.py:469-518)
   - Attach vectors to ranked results
   - MMRReranker.rerank()
   - Log MMR scores to analytics
   ↓
5. Final Results (top-10)
   - Cached for generate mode
   - Sent to frontend as news cards
```

### Integration Points

**Before MMR**: `ranking.py:467`
```python
ranked = sorted(filtered, key=lambda x: x['ranking']["score"], reverse=True)
```

**MMR Re-ranking**: `ranking.py:469-518`
- Checks if MMR enabled and vectors available
- Calls `MMRReranker.rerank()` with top-ranked documents
- Logs diversity scores to `ranking_scores` table

**After MMR**: `ranking.py:505`
```python
self.handler.final_ranked_answers = reranked_results
```

### Configuration Parameters

**`config/config_retrieval.yaml`**:
```yaml
mmr_params:
  enabled: true           # Enable/disable MMR
  lambda: 0.7             # Default trade-off (0.0-1.0)
  threshold: 3            # Min results needed
  include_vectors: true   # Retrieve vectors from Qdrant
```

**Feature Flags**:
- Set `enabled: false` to disable MMR (uses standard ranking)
- Set `include_vectors: false` to skip vector retrieval (MMR won't run)

---

## Code Structure

### Class: `MMRReranker` (`mmr.py`)

**Methods**:

1. `__init__(lambda_param, query)`
   - Initialize with λ parameter
   - Detect intent and adjust λ
   - Logs intent detection results

2. `_detect_intent_and_adjust_lambda(query)`
   - Scans query for intent indicators
   - Returns adjusted λ value (0.5, 0.7, or 0.8)

3. `cosine_similarity(vec1, vec2)`
   - Calculates cosine similarity between vectors
   - Returns float in [0, 1] range
   - Handles edge cases (zero vectors)

4. `rerank(ranked_results, query_vector, top_k)`
   - Main MMR algorithm
   - Returns (reranked_results, mmr_scores)
   - Logs diversity improvement metrics

### Integration: `ranking.py`

**Lines 373-381**: Extract vectors from search results
```python
self.url_to_vector = {}
for item in self.items:
    if len(item) == 5:  # [url, json_str, name, site, vector]
        url, _, _, _, vector = item
        self.url_to_vector[url] = vector
```

**Lines 469-518**: Apply MMR if enabled
```python
if mmr_enabled and len(ranked) > mmr_threshold and self.url_to_vector:
    # Attach vectors to results
    # Call MMRReranker
    # Log to analytics
```

---

## Analytics Schema

### Table: `ranking_scores`

**MMR-Related Columns**:
- `mmr_diversity_score DOUBLE PRECISION` - MMR score for this document
- `ranking_position INT` - Final position after MMR (0-indexed)
- `ranking_method VARCHAR(20)` - Set to "mmr" for MMR-reranked results

**Logging**: `query_logger.log_mmr_score()`
- Called after MMR re-ranking completes
- Logs MMR score for each document
- Associates with query_id for training data

---

## Testing Strategy

### Unit Tests (Week 3)

1. **MMR Algorithm**:
   - Test iterative selection logic
   - Verify diversity improvement (avg similarity reduction)
   - Test edge cases (0, 1, 2 documents)

2. **Intent Detection**:
   - Test SPECIFIC queries → λ=0.8
   - Test EXPLORATORY queries → λ=0.5
   - Test mixed/unclear → λ=0.7

3. **Vector Retrieval**:
   - Verify `with_vectors=True` returns vectors
   - Test backward compatibility (4-tuple without vectors)

### Integration Tests (Week 3)

1. **End-to-End Flow**:
   - Query → Retrieval → Ranking → MMR → Results
   - Verify vectors attached to results
   - Check analytics logging

2. **Mode Coverage**:
   - Test list mode (MMR should apply)
   - Test summarize mode (MMR should apply)
   - Test generate mode (uses cached MMR results)

3. **Config Toggles**:
   - `mmr_params.enabled: false` → standard ranking
   - `mmr_params.include_vectors: false` → no MMR
   - `bm25_params.enabled: false` → test independence

### Production Validation (Week 3)

1. **A/B Testing**:
   - 50% traffic: MMR enabled
   - 50% traffic: Old LLM diversity (uncomment code in `generate_answer.py`)
   - Metrics: CTR, dwell time, diversity (manual review)

2. **Performance Monitoring**:
   - Query latency (expect 40% reduction)
   - Cost per query (expect 40% reduction with BM25+MMR)
   - Error rate (should be 0% - no LLM calls for diversity)

---

## Performance Metrics

### Expected Results (Week 3)

| Metric | Before (LLM Diversity) | After (MMR) | Change |
|--------|------------------------|-------------|--------|
| **Cost per query** | $1.20 | $0.70 | -40% |
| **Latency** | 15-25s | 8-12s | -40% |
| **Diversity (avg similarity)** | ~0.65 | ~0.45 | -30% |
| **Consistency** | Variable (LLM) | Deterministic | ✓ |

### Accuracy Validation

**Manual Review** (50 diverse queries):
- Compare MMR results vs LLM diversity results
- Rate diversity on 1-5 scale
- Rate relevance on 1-5 scale
- Expected: Similar or better diversity, equal relevance

---

## Rollback Plan

### Quick Disable

**Option 1**: Config toggle
```yaml
# config/config_retrieval.yaml
mmr_params:
  enabled: false  # Disables MMR, uses standard ranking
```

**Option 2**: Re-enable LLM diversity
```python
# methods/generate_answer.py
# Uncomment lines 380-395 (old diversity code)
```

### Gradual Rollback

1. Set `mmr_params.lambda: 1.0` → Pure relevance (no diversity penalty)
2. Monitor for 24 hours
3. If stable, set `enabled: false` to fully disable
4. Re-enable LLM diversity if needed

---

## Future Enhancements (Week 4+)

### ML-Based Lambda Tuning

Replace rule-based intent detection with **XGBoost Regressor** for continuous λ prediction.

**Input Features** (12-15 features):
- **Query text**: length, word_count, has_quotes, has_numbers, has_question_words, keyword_count
- **Embedding**: embedding_entropy (semantic specificity), vector_norm
- **User history**: past_click_rate, avg_dwell_time, query_frequency
- **Temporal**: time_of_day, day_of_week, is_trending_topic
- **Current intent**: specific_indicator_count, exploratory_indicator_count

**Training Labels**:
- `optimal_lambda` (FLOAT, 0.0-1.0) learned from user engagement metrics
- High CTR + high dwell time with λ=X → Label: X is optimal
- Calculated from analytics database: `queries` + `user_interactions` tables

**Output**:
- Continuous λ prediction (e.g., 0.63, 0.72, 0.85) instead of categorical (0.5, 0.7, 0.8)
- More precise tuning for each individual query

**Deployment**:
- **Phase 1**: Shadow mode (log ML predictions, use rule-based)
- **Phase 2**: A/B testing (50% ML, 50% rule-based)
- **Phase 3**: Gradual rollout (10% → 100%)

**Expected Impact**:
- +8-12% CTR improvement (better intent matching)
- +10-15% dwell time improvement (more relevant results)
- ~85% lambda accuracy (vs ~70% for rule-based)

**Rollback**: `mmr_params.use_ml_lambda: false` in config

**See**: `docs/archive/algo-reviews/Week4_ML_Enhancements.md` for complete implementation plan

### Cascading MMR + XGBoost Ranking

**Architecture**:
```
Retrieval (pg_bigm + Vector)
    ↓
MMR Diversity Re-ranking
    ↓
XGBoost Ranking (ALL results, 0.5-1s)
    ↓
LLM Refinement (top-10 ONLY if confidence < 0.8, 2-3s)
    ↓
Final Results
```

**XGBoost Features** (25-30 features):
- Retrieval scores (vector_similarity, bm25_score, keyword_overlap)
- Document quality (length, recency, schema_completeness)
- Ranking context (position, relative_score)
- MMR diversity score
- Historical CTR/dwell time for this URL

**Expected Impact**:
- **88% cost reduction** from baseline ($1.20 → $0.15 per query)
- **75% latency reduction** (20s → 5s)
- 0-10 LLM calls instead of 50 (80-100% ranking cost savings)

**Timeline**: Week 5-8 (after collecting 50,000+ query-document pairs)

**See**: `docs/archive/algo-reviews/Week4_ML_Enhancements.md` for complete XGBoost implementation plan

---

## Changelog

### 2025-01-19 - Initial Implementation
- ✅ Created `core/mmr.py` with MMR algorithm
- ✅ Integrated into `core/ranking.py` (lines 469-518)
- ✅ Added vector retrieval in `retrieval_providers/qdrant.py`
- ✅ Configured in `config/config_retrieval.yaml`
- ✅ Commented out old LLM diversity in `generate_answer.py`
- ✅ Added analytics logging `query_logger.log_mmr_score()`
- ✅ Intent-based λ tuning (SPECIFIC, EXPLORATORY, BALANCED)

### 2026-03-19 - PostgreSQL Vector Retrieval Fix
- ✅ `postgres_client.py` now returns 5-tuple `[url, schema, title, source, vector]` when `include_vectors=True`
- ✅ pgvector results normalised to `list[float]` for `cosine_similarity()` compatibility
- ✅ Backward compatible: 4-tuple returned without `include_vectors` kwarg
- ✅ 19 unit tests in `tests/unit/test_mmr_vector_retrieval.py`
- ✅ MMR no longer silently skipped on PostgreSQL

### 2026-03-27 — R1 dead code cleanup + R5 hot path fix
- ✅ `post_ranking.py`: removed `apply_mmr_reranking()` (never called, dead code)
- ✅ `mmr.py`: `_log_diversity_metrics()` file I/O (`open(file, 'a')`) → `logger.info()` (hot path optimization)
- ✅ `mmr.py`: `cosine_similarity()` kept — used by 3 unit tests even though production uses matrix approach
- ✅ `xgboost_ranker.py`: simplified to dict-primary (kept list handling for postgres_client compatibility)

### Pending
- ⏳ A/B testing (MMR vs no diversity)
- ⏳ Parameter tuning based on user feedback
- ⏳ Production monitoring and validation
