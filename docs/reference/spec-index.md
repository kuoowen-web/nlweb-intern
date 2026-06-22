# Spec 索引

> Zoe 派工挑 spec 時的 **eager 查表**（取代裸 `ls docs/specs/`）。
> 每條 = 檔名 → 一句話涵蓋範圍 → 狀態。派工該模組時，查表選相關 spec 路徑寫進 subagent prompt。
> **不憑檔名猜涵蓋範圍** —— 查這張表。新增 spec 時順手加一行（呼應 reconcile 紀律）。
> 狀態標記：✅ active｜⚠️ 部分過時（標明哪段）｜🪦 退役/重定向。
>
> **🪦 全域注記 — Qdrant 已徹底廢除（2026-06-22）**：Qdrant 向量 DB + 整套上游 WebSocket chat 子系統已從 code 完全移除（現役檢索=PostgreSQL pgvector only，見 `lessons-infra-deploy`）。**多份 spec 仍殘留 Qdrant 術語**（indexing-spec ~58、login-spec ~11、bm25/reranking/mmr/xgboost 等演算法 spec 的歷史脈絡）——這些是**歷史紀錄非現行行為**，待下次 `/learn specs` 全量掃時系統性清理（演算法 spec 的 Qdrant 段標歷史、indexing-spec 儲存層段已部分標 retired）。不影響各 spec 的演算法/契約內容正確性。

---

## Reasoning / Research（LR + DR 核心）

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `live-research-spec.md` | LR（Live Research Beta）六階段管線、BAB loop、grounding、publish gate、DR-parity；**最大最核心 spec（114KB）** | ✅（2026-06-15 re-sync） |
| `reasoning-spec.md` | DR（Deep Research）Analyst/Critic/Writer、推論鏈、source tier（**注意 source tier 為 deprecate 中機制，見下方注記**） | ✅（2026-06-17 re-sync） |
| `mock-bab-playbook.md` | Mock BAB 自驗法 — 解耦 evidence 蒐集（貴）vs Pipeline 處理（便宜），可重複自驗 | ✅ |
| `kg-spec.md` | Knowledge Graph 視覺化功能實作 | ✅ |
| `kg-editing-spec.md` | KG 編輯模式（**已退役/重定向，2026-05-25**） | 🪦 退役 |

## Ranking / Retrieval

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `xgboost-spec.md` | XGBoost ranking 實作 | ✅ |
| `bm25-spec.md` | BM25 實作 | ⚠️ 已過時（2026-06-15，實作已改 pg_bigm） |
| `reranking-spec.md` | Reranking pipeline | ⚠️ Stage 1（Qdrant）段過時（2026-06-15） |
| `mmr-spec.md` | MMR（Maximal Marginal Relevance）多元性排序 | ✅ |

## Indexing / Data Pipeline

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `indexing-spec.md` | M0 Indexing 模組、來源管理、品質閘 | ⚠️ 部分章節仍用 Qdrant 術語（The Map） |
| `bulk-load-spec.md` | Bulk Load Pipeline（TSV → embedding → PG） | ✅ |
| `database-spec.md` | DB schema 管理、alembic 方案 B | ✅ active |
| `code-in-sqlite.md` | 程式碼索引系統（indexer.py 的 SQLite FTS5） | ✅ |

## Frontend / UX

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `frontend-spec.md` | 前端功能總規格（**最大前端 spec，61KB**） | ✅ |
| `help-center-spec.md` | Help Center | ✅ |

## Auth / Session / Multi-tenant

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `login-spec.md` | Login 系統（含 onboarding、JWT、user switch） | ✅ |
| `session-spec.md` | Session 系統（持久化、cross-user 防護；**80KB**） | ✅ |
| `org-multi-tenant-spec.md` | 組織 / 多租戶 | ✅ |
| `private-docs-spec.md` | Private Documents（檔案上傳、隔離） | ✅ |

## Security / Guardrail

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `guardrail-spec.md` | 護欄系統（QuerySanitizer、injection、PII、rate limit、quality gate） | ✅ |
| `csp-security-spec.md` | CSP / Security Headers | ✅ |

## Analytics / 維運

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `analytics-spec.md` | Analytics 系統（欄名以實際 schema 為準） | ✅ |
| `everything-cc-manual.md` | Claude Code 配置使用手冊 | ✅ |

---

## ⚠️ 重要注記：source tier / 來源分級為 deprecate 中機制

`reasoning-spec.md`、`indexing-spec.md`、`guardrail-spec.md`、`systemmap.md` 仍描述 source tier（來源分級 Tier 1-5 / 「未經證實」警語）為 active。**實際狀態**（2026-06-18 驗證）：
- hard filtering 已於 2026-04 移除（`reasoning/filters/source_tier.py` 只剩 enrich 前綴）
- 產品對外**不宣稱**來源分級
- CEO 拍板**徹底廢除**（2026-06-18），但 code 移除為獨立 sprint（射程已擱置待排）

派工/引用這幾個 spec 的 source tier 章節時，知道它是 stale，不要當 active 機制描述。
