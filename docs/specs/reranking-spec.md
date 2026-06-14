## 一、現有 Reranking Pipeline 完整流程

### **完整的排序流程（5 個階段）**

```
User Query
    ↓
[Stage 1] Vector DB Retrieval (Qdrant/Elasticsearch/etc.)
    - Embedding vector search (語義相似度)
    - Returns: Top 50-200 results
    ↓
[Stage 2] Hybrid Scoring (BM25 + Intent Detection)
    - BM25 keyword matching (關鍵字相關性)
    - Intent detection → Dynamic α/β (EXACT_MATCH/SEMANTIC/BALANCED)
    - Score fusion: final_score = α * vector_score + β * bm25_score
    - Temporal boosting (recency multiplier for temporal queries)
    ↓
[Stage 3] LLM Ranking (Semantic Relevance)
    - LLM scores each document for semantic relevance (0-100)
    - Generates description snippet
    - Returns: Ranked results with LLM scores
    ↓
[Stage 4] XGBoost Re-ranking (ML-based Optimization) - Phase A Shadow Mode
    - Feature extraction (29 features from retrieval + LLM scores)
    - ML prediction (relevance score 0-1)
    - Confidence calculation
    - Phase A: Shadow mode (log only, don't change ranking)
    - Phase C: Production mode (re-rank by XGBoost scores)
    ↓
[Stage 5] MMR Diversity Re-ranking
    - Balance relevance vs diversity
    - Intent-based λ tuning (EXPLORATORY=0.5, SPECIFIC=0.8)
    - Returns: Final top 10 diverse results
    ↓
Final Results (10 results)
```

---

## 二、各階段詳細說明

### **Stage 1: Vector DB Retrieval**

**檔案位置：** retrieval_providers/qdrant.py, core/retriever.py

**功能：**

- Embedding-based semantic search
- 支援多種 Vector DB (Qdrant, Elasticsearch, Milvus, Azure AI Search, etc.)
- 返回格式：List[Dict] with retrieval_scores

**輸出的 metadata：**

```python
{
    'url': str,
    'title': str,
    'site': str,
    'schema_json': str,  # Schema.org structured data
    'retrieval_scores': {
        'vector_score': float,  # Cosine similarity (0-1)
        'bm25_score': 0.0,      # 尚未計算
        'keyword_boost': 0.0,   # 尚未計算
        'temporal_boost': 0.0,  # 尚未計算
        'final_retrieval_score': float  # = vector_score
    },
    'vector': List[float]  # Optional, for MMR
}
```

---

### **Stage 2: Hybrid Scoring (BM25 + Intent)**

**檔案位置：** retrieval_providers/qdrant.py:555-650 (hybrid_search method)

**關鍵創新：Intent Detection**

```python
def _detect_query_intent(query):
    # EXACT_MATCH: 有引號、數字、hashtag、專有名詞 → α=0.4, β=0.6
    # SEMANTIC: 有問句詞、概念詞、短查詢 → α=0.7, β=0.3
    # BALANCED: 其他 → α=0.6, β=0.4 (default)
    return (alpha, beta)
```

**BM25 計算：**

- Tokenization: Chinese (2-4 char), English (2+ char)
- Title 加權 3x
- Corpus statistics: avg_doc_length, term_doc_counts (動態計算)

**Score Fusion：**

```python
final_score = α * vector_score + β * bm25_score
```

**Temporal Boosting（針對時間敏感查詢）：**

```python
if is_temporal_query(['最新', 'latest', 'recent']):
    recency_multiplier = calculate_recency(days_old)
    final_score *= recency_multiplier
```

**更新後的 retrieval_scores：**

```python
'retrieval_scores': {
    'vector_score': float,
    'bm25_score': float,           # 新增
    'keyword_boost': 0.0,          # deprecated
    'temporal_boost': float,       # recency_multiplier - 1.0
    'final_retrieval_score': float # α*vector + β*bm25
}
```

---

### **Stage 3: LLM Ranking**

**檔案位置：** core/ranking.py:205-291 (rankItem method)

**功能：**

- 使用 LLM 對每個文件評分（0-100）
- 生成 description snippet
- 支援不同的 ranking prompt（product-focused, news, etc.）

**輸出格式：**

```python
{
    'url': str,
    'name': str,
    'site': str,
    'ranking': {
        'score': int,         # 0-100
        'description': str    # LLM 生成的摘要
    },
    'schema_object': dict,
    'retrieval_scores': dict,  # 從 Stage 2 繼承
    'vector': List[float]      # Optional, for MMR
}
```

---

### **Stage 4: XGBoost Re-ranking**

**檔案位置：** core/xgboost_ranker.py, core/ranking.py:546-578

**關鍵決策：位置在 LLM 之後、MMR 之前**

原因：

1. XGBoost 需要 LLM scores 作為特徵
2. MMR 需要最終的 relevance ranking

**29 個特徵（Phase A）：**

|Category|Features|Source|
|---|---|---|
|Query (6)|query_length, word_count, has_quotes, has_numbers, has_question_words, keyword_count|Query text|
|Document (8)|doc_length, recency_days, has_author, has_publication_date, schema_completeness, title_length, description_length, url_length|schema_object|
|Retrieval (7)|vector_score, bm25_score, keyword_boost, temporal_boost, final_retrieval_score, keyword_overlap_ratio, title_exact_match|retrieval_scores|
|Ranking (6)|retrieval_position, ranking_position, llm_final_score, relative_score_to_top, score_percentile, position_change|LLM ranking|
|MMR (2)|mmr_diversity_score, detected_intent|Not available in Phase A|

**Phase A 狀態：Shadow Mode**

- 計算 XGBoost scores，但不改變排序
- 記錄到 analytics database
- 用於驗證模型效果

**Phase C 生產模式：**

```python
if xgboost_confidence > 0.8:
    # 高信心：使用 XGBoost ranking
    reranked_results = sort_by_xgboost_score(results)
else:
    # 低信心：保留 LLM ranking
    reranked_results = results
```

---

### **Stage 5: MMR Diversity Re-ranking**

**檔案位置：** core/mmr.py, core/ranking.py:584-634

**功能：**

- 平衡 relevance 與 diversity
- 防止重複相似的結果

**公式：**

```
MMR = λ * Relevance(doc, query) - (1-λ) * max(Similarity(doc, selected_docs))
```

**Intent-based λ 調整：**

- SPECIFIC intent (如 "how to..."): λ=0.8 (優先相關性)
- EXPLORATORY intent (如 "best options"): λ=0.5 (優先多樣性)
- BALANCED: λ=0.7 (default)

**需要的數據：**

- vector: Document embedding (1536 dims)
- ranking.score: LLM or XGBoost score
---

*更新：2026-02-04*
