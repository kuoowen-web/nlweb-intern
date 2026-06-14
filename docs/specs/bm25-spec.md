# BM25 Implementation Documentation

**Algorithm Type:** Text Retrieval Ranking
**Status:** Implemented (Ready for Testing)
**Implementation File:** `code/python/core/bm25.py`
**Integration Point:** `code/python/retrieval_providers/qdrant.py`
**Configuration File:** `config/config_retrieval.yaml`
**Test File:** `code/python/testing/test_bm25.py`

---

## Purpose

Replace simple keyword boosting with BM25 (Best Match 25) algorithm to:
- Improve keyword matching precision
- Reduce LLM usage for keyword scoring
- Provide more consistent and interpretable relevance scores

---

## Algorithm Overview

BM25 is a probabilistic ranking function based on term frequency (TF) and inverse document frequency (IDF).

### Formula

For a query Q containing terms q₁, q₂, ..., qₙ and a document D:

```
BM25(D, Q) = Σ IDF(qᵢ) × (f(qᵢ, D) × (k₁ + 1)) / (f(qᵢ, D) + k₁ × (1 - b + b × (|D| / avgdl)))
```

Where:
- `f(qᵢ, D)` = term frequency of qᵢ in document D
- `|D|` = length of document D (in tokens)
- `avgdl` = average document length in the corpus
- `k₁` = term saturation parameter (controls TF saturation)
- `b` = length normalization parameter (controls document length penalty)
- `IDF(qᵢ)` = log((N - n(qᵢ) + 0.5) / (n(qᵢ) + 0.5) + 1)
  - `N` = total number of documents in corpus
  - `n(qᵢ)` = number of documents containing qᵢ

---

## Parameters

### Default Values
```yaml
k1: 1.5              # Term saturation (typical range: 1.2-2.0)
b: 0.75              # Length normalization (typical range: 0.5-0.9)
alpha: 0.6           # Vector score weight
beta: 0.4            # BM25 score weight
```

### Parameter Tuning Guide

**k₁ (Term Saturation):**
- Higher k₁ (e.g., 2.0): Gives more weight to term frequency
- Lower k₁ (e.g., 1.2): Saturation kicks in earlier
- Use case: Increase if users expect exact keyword matching

**b (Length Normalization):**
- Higher b (e.g., 0.9): Penalize long documents more
- Lower b (e.g., 0.5): Less penalty for document length
- Use case: Decrease if short snippets perform poorly

**α/β (Score Combination):**
- Higher α (e.g., 0.7): Favor semantic similarity (vector)
- Higher β (e.g., 0.6): Favor keyword matching (BM25)
- Constraint: α + β = 1.0
- **Dynamic Adjustment**: α/β values are automatically adjusted based on query intent (see Intent Detection section)

---

## Intent Detection & Dynamic α/β Adjustment

**Purpose:** Automatically detect user search intent and adjust the balance between vector (semantic) and BM25 (keyword) scoring.

**Implementation Location:** `code/python/retrieval_providers/qdrant.py:555-619` (`_detect_query_intent()` method)

### Query Intent Types

**1. EXACT_MATCH Intent** (α=0.4, β=0.6)
- User expects precise keyword matching
- BM25 score weighted higher than vector score
- **Indicators:**
  - Quoted strings (e.g., "exact phrase")
  - Numbers or dates (e.g., "2024", "77期")
  - Hashtags (e.g., "#martech")
  - Proper nouns (consecutive capital letters, e.g., "Tiktok", "AI")
  - Long queries (10+ characters)

**2. SEMANTIC Intent** (α=0.7, β=0.3)
- User expects broad conceptual understanding
- Vector score weighted higher than BM25 score
- **Indicators:**
  - Question words (什麼, 如何, 為什麼, what, how, why)
  - Concept words (概念, 趨勢, 影響, 原因, concept, trend, impact)
  - Short queries (< 6 characters)

**3. BALANCED Intent** (α=default, β=default)
- Mixed or unclear intent
- Use configured default values (typically α=0.6, β=0.4)
- **Triggers:**
  - Similar exact match and semantic scores
  - General queries without strong indicators

### Scoring Algorithm

```python
exact_score = 0
semantic_score = 0

# Exact match features (weighted scoring)
if has_quotes: exact_score += 3
if has_numbers: exact_score += 2
if has_hashtags: exact_score += 2
if has_proper_nouns: exact_score += 1
if len(query) >= 10: exact_score += 1

# Semantic features (weighted scoring)
if has_question_words: semantic_score += 3
if has_concept_words: semantic_score += 2
if len(query) < 6: semantic_score += 1

# Intent classification (2-point threshold)
if exact_score > semantic_score + 2:
    return (0.4, 0.6)  # EXACT_MATCH
elif semantic_score > exact_score + 2:
    return (0.7, 0.3)  # SEMANTIC
else:
    return (alpha_default, beta_default)  # BALANCED
```

### Examples

**Example 1: Exact Match Query**
```
Query: "Martech雙周報第77期"
Features: Quoted string (+3), Number "77" (+2), Long query (+1)
Scores: exact=6, semantic=0
Intent: EXACT_MATCH → α=0.4, β=0.6 (prioritize BM25)
Result: Exact title match ranks top
```

**Example 2: Semantic Query**
```
Query: "什麼是AI趨勢"
Features: Question word "什麼" (+3), Concept word "趨勢" (+2)
Scores: exact=0, semantic=5
Intent: SEMANTIC → α=0.7, β=0.3 (prioritize vector)
Result: Conceptually related articles rank higher
```

**Example 3: Balanced Query**
```
Query: "AI martech工具"
Features: Proper noun "AI" (+1)
Scores: exact=1, semantic=0
Intent: BALANCED → α=0.6, β=0.4 (use defaults)
Result: Balanced semantic and keyword matching
```

### Implementation Status

**✅ Phase 1: Rule-Based Intent Detection (Week 2)**
- Simple scoring system based on query features
- Hardcoded α/β values for each intent type
- Minimal computational overhead (< 5ms)
- No training data required

**⏳ Phase 2: ML-Based Intent Classification (Week 4+)**
- Train XGBoost classifier on user interaction data
- Features: query length, has_quotes, CTR by intent type, etc.
- Predict optimal α/β values (continuous, not categorical)
- Requires 10,000+ labeled queries

### Validation Approach

**Manual Testing (Week 2-3):**
- Collect 50 diverse queries
- Manually label expected intent
- Verify intent detection accuracy
- Tune scoring thresholds if needed

**A/B Testing (Week 3-4):**
- Compare fixed α/β (0.6/0.4) vs intent-based adjustment
- Metrics: CTR, dwell time, user satisfaction
- Validate if dynamic adjustment improves results

**ML Training Data (Week 4+):**
- Log detected intent and α/β values in analytics database
- Track user engagement by intent type
- Use as features for XGBoost ranking model

---

## Implementation Details

### Tokenization Strategy

**Chinese Text:**
- Extract 2-4 character sequences using regex: `[\u4e00-\u9fff]{2,4}`
- No stemming or lemmatization
- Lowercase not applicable

**English Text:**
- Extract words (2+ characters) using regex: `[a-zA-Z]{2,}`
- Convert to lowercase
- No stemming (keep implementation simple)

**Mixed Text:**
- Process both Chinese and English tokens separately
- Combine into unified token list

### Document Fields

Index the following fields from Schema.org markup:
- `name` (title) - **Weight: 3x**
- `description` or `articleBody` (content) - **Weight: 1x**

Field weighting applied by duplicating title tokens 3x in the document representation.

### Corpus Statistics

**Document Count (N):**
- Use the total number of documents returned by Qdrant search
- Typical range: 50-500 documents per query

**Average Document Length (avgdl):**
- Calculate on-the-fly from current search results
- Cache per query (not globally)

**Term Document Frequency n(qᵢ):**
- Count how many documents contain term qᵢ
- Minimum threshold: 2 documents (avoid division by zero)

---

## Integration Architecture

### Current System (Before BM25)

```
Vector Search (Qdrant)
    ↓
Keyword Extraction
    ↓
Simple Keyword Boosting
    final_score = base_score * (1 + keyword_boost)
    ↓
Temporal Boosting (if applicable)
    ↓
Sort by final_score
```

### New System (With BM25 + Intent Detection)

```
Vector Search (Qdrant)
    ↓
Keyword Extraction
    ↓
Intent Detection (NEW - Week 2)
    detect_query_intent() → (α, β)
    EXACT_MATCH: α=0.4, β=0.6
    SEMANTIC: α=0.7, β=0.3
    BALANCED: α=default, β=default
    ↓
BM25 Scoring (NEW - Week 2)
    bm25_score = BM25Scorer.calculate_score(query_tokens, doc_text, avgdl, N)
    ↓
Score Combination (with dynamic α/β)
    final_score = α * vector_score + β * bm25_score
    ↓
Temporal Boosting (if applicable)
    final_score *= recency_multiplier
    ↓
Sort by final_score
```

---

## Code Structure

### Module: `code/python/core/bm25.py`

```python
class BM25Scorer:
    """BM25 ranking algorithm for hybrid search."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        """Initialize with BM25 parameters."""

    def tokenize(self, text: str) -> List[str]:
        """Extract Chinese and English tokens from text."""

    def calculate_idf(self, term: str, doc_count: int, term_doc_count: int) -> float:
        """Calculate inverse document frequency for a term."""

    def calculate_score(
        self,
        query_tokens: List[str],
        document_text: str,
        avg_doc_length: float,
        corpus_size: int,
        term_doc_counts: Dict[str, int]
    ) -> float:
        """Calculate BM25 score for a query-document pair."""
```

### Integration: `code/python/retrieval_providers/qdrant.py`

**Modified Function:** `hybrid_search()` (lines 599-913)

**New Methods:**
1. **`_detect_query_intent()`** (lines 555-619):
   - Analyzes query for exact match features (quotes, numbers, hashtags, proper nouns, length)
   - Analyzes query for semantic features (question words, concept words, short length)
   - Calculates intent scores and returns adjusted (α, β) values
   - Intent types: EXACT_MATCH (0.4, 0.6), SEMANTIC (0.7, 0.3), BALANCED (default)

**Integration Changes:**
1. **Lines 692-701**: Intent detection and BM25 configuration
   - Load BM25 parameters from CONFIG
   - Call `_detect_query_intent()` to get dynamic α/β values
   - Print intent detection results to terminal

2. **Lines 706-723**: BM25 scorer initialization
   - Create BM25Scorer instance with k1, b parameters
   - Prepare documents for corpus statistics calculation
   - Calculate avg_doc_length and term_doc_counts

3. **Lines 725-733**: Score storage dictionary
   - Create `point_scores = {}` to store BM25/keyword scores by URL
   - Avoids modifying immutable ScoredPoint objects

4. **Lines 781-798**: BM25 scoring per document
   - Combine title (3x weight) and description into doc_text
   - Calculate BM25 score using query tokens and corpus stats
   - Combine with vector score: `final_score = α * vector_score + β * bm25_score`

5. **Lines 904-927**: Terminal output logging
   - Print BM25 score breakdown for top 5 results
   - Show vector score, BM25 score, α/β values, final score
   - Display score combination formula

6. **Lines 928-934**: Analytics database logging
   - Log bm25_score, keyword_boost, vector_score, final_score to database
   - Retrieve scores from point_scores dictionary by URL

---

## Analytics Tracking

### Database Schema

Table: `retrieved_documents`

Columns used:
- `vector_similarity` - Cosine similarity from embedding search
- `bm25_score` - BM25 relevance score (NEW - previously unused)
- `keyword_boost_score` - OLD simple boosting (deprecated after BM25)
- `final_retrieval_score` - Combined score (α * vector + β * bm25)

### Logging Location

File: `code/python/retrieval_providers/qdrant.py`, lines 903-913

```python
query_logger.log_retrieved_document(
    query_id=query_id,
    doc_url=result_url,
    vector_similarity=vector_score,
    bm25_score=bm25_score,  # NOW POPULATED
    keyword_boost_score=0.0,  # DEPRECATED
    final_retrieval_score=final_score
)
```

---

## Testing Strategy

### Unit Tests

File: `code/python/testing/test_bm25.py` (to be created)

Test cases:
1. **Tokenization**: Verify Chinese/English token extraction
2. **IDF Calculation**: Test with known corpus statistics
3. **BM25 Score**: Test with known query-document pairs
4. **Edge Cases**: Empty query, no matching terms, single document corpus

### Integration Tests

File: `code/python/testing/test_qdrant_bm25.py` (to be created)

Test cases:
1. **Score Combination**: Verify α * vector + β * bm25 formula
2. **Analytics Logging**: Confirm bm25_score is logged correctly
3. **Ranking Consistency**: Compare BM25 results vs old keyword boosting
4. **Chinese Query**: Test with Chinese keywords
5. **English Query**: Test with English keywords
6. **Mixed Query**: Test with Chinese + English

### A/B Testing (Week 3)

Metrics to compare:
- Click-through rate (CTR)
- Average dwell time
- User satisfaction (implicit feedback)
- Query latency
- LLM API cost

---

## Performance Considerations

### Computational Complexity

- **Time Complexity**: O(n × m)
  - n = number of documents (50-500)
  - m = average number of unique query terms (5-15)
- **Expected Latency**: < 50ms for typical queries

### Optimization Opportunities

1. **Token Caching**: Cache tokenized documents (if documents repeat across queries)
2. **IDF Precomputation**: Cache IDF values per term (within a query session)
3. **Vectorized Operations**: Use NumPy for batch TF calculations

---

## Migration Plan

### Phase 1: Parallel Deployment (Week 2)

- Deploy BM25 alongside existing keyword boosting
- Log both `bm25_score` and `keyword_boost_score`
- Use keyword boosting for ranking (no user impact)
- Collect data for comparison

### Phase 2: Shadow Mode (Week 2-3)

- Calculate BM25 scores for all queries
- Compare BM25 rankings vs keyword boosting rankings
- Identify queries where BM25 performs better/worse

### Phase 3: Full Deployment (Week 3)

- Switch to BM25 for all queries
- Remove keyword boosting code
- Update LLM prompts to remove keyword scoring instructions

---

## Expected Impact

### Cost Reduction (Week 3)
- **Before**: LLM scores keywords for top 50-80 results
- **After**: BM25 handles keyword scoring, LLM focuses on semantic relevance
- **Savings**: 40% reduction in LLM API calls

### Latency Reduction (Week 3)
- **Before**: 15-25 seconds per query
- **After**: 8-12 seconds per query (40% reduction)
- **Reason**: Fewer LLM scoring iterations

### Accuracy Improvement
- **Consistency**: BM25 produces deterministic scores (no LLM variance)
- **Interpretability**: Easy to debug why a document ranked high
- **No Reward Hacking**: Algorithm-based, not learned from flawed proxy metrics

---

## Rollback Plan

If BM25 performance is worse than keyword boosting:

1. **Immediate**: Switch back to keyword boosting in config
   ```yaml
   use_bm25: false  # Add feature flag
   ```

2. **Short-term**: Investigate failure cases
   - Analyze queries where BM25 performed poorly
   - Check if parameter tuning (k₁, b, α, β) can help

3. **Long-term**: Hybrid approach
   - Use BM25 for English queries
   - Use keyword boosting for Chinese queries
   - Or vice versa based on data

---

## References

- **Original Paper**: Robertson, S. E., & Zaragoza, H. (2009). "The Probabilistic Relevance Framework: BM25 and Beyond"
- **Implementation Guide**: https://en.wikipedia.org/wiki/Okapi_BM25
- **Parameter Tuning**: Trotman, A., et al. (2014). "Improvements to BM25 and Language Models Examined"

---

## Implementation Summary

### Completed (Phase 1-2)
✅ **BM25 Core Module** (`code/python/core/bm25.py`)
- BM25Scorer class with k1, b parameters
- Tokenization for Chinese (2-4 char) and English (2+ char)
- IDF calculation with proper handling of rare/common terms
- BM25 scoring formula with term saturation and length normalization
- Corpus statistics calculation (avg_doc_length, term_doc_counts)

✅ **Qdrant Integration** (`code/python/retrieval_providers/qdrant.py`)
- Feature flag: `bm25_params.enabled` in config (default: true)
- Hybrid scoring: `α * vector_score + β * bm25_score`
- Intent detection: Dynamic α/β adjustment based on query features
  - EXACT_MATCH intent: α=0.4, β=0.6 (prioritize keyword matching)
  - SEMANTIC intent: α=0.7, β=0.3 (prioritize semantic similarity)
  - BALANCED intent: Use default α/β values
- Title weighting: 3x repetition in document text
- Fallback to old keyword boosting if BM25 disabled
- Analytics logging: bm25_score, keyword_boost_score, final_score
- Terminal output: BM25 score breakdown for top 5 results

✅ **Configuration** (`config/config_retrieval.yaml`)
```yaml
bm25_params:
  enabled: true    # Feature flag
  k1: 1.5          # Term saturation
  b: 0.75          # Length normalization
  alpha: 0.6       # Vector weight
  beta: 0.4        # BM25 weight
```

✅ **Unit Tests** (`code/python/testing/test_bm25.py`)
- 19 tests covering tokenization, IDF, scoring, parameters
- All tests passing ✅

### Pending (Phase 3)
⏳ **End-to-End Testing**
- Test with real Chinese queries
- Test with real English queries
- Test with mixed language queries
- Verify analytics logging

⏳ **A/B Testing**
- Compare BM25 vs old keyword boosting
- Tune α/β ratio (vector vs BM25 weight)
- Tune k1, b parameters based on user feedback

⏳ **Performance Validation**
- Measure latency impact
- Measure cost reduction (40% target)
- Measure accuracy improvement

## Changelog

| Date | Version | Changes | Author |
|------|---------|---------|--------|
| 2025-11-18 | 1.0 | Initial documentation | Claude |
| 2025-11-18 | 1.1 | Implementation complete (Phase 1-2) | Claude |
| 2025-11-18 | 1.2 | Added intent detection for dynamic α/β adjustment | Claude |

---

## Notes

- **Do NOT use BM25 for temporal boosting** - Keep time decay as separate function
- **Do NOT use BM25 for domain filtering** - Keep as boolean/categorical logic
- **BM25 is for content relevance only** - Keyword matching between query and document text
