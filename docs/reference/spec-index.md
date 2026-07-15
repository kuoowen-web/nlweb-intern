# Spec 索引

> Zoe 派工挑 spec 時的 **eager 查表**（取代裸 `ls docs/specs/`）。
> 每條 = 檔名 → 一句話涵蓋範圍 → 狀態。派工該模組時，查表選相關 spec 路徑寫進 subagent prompt。
> **不憑檔名猜涵蓋範圍** —— 查這張表。新增 spec 時順手加一行（呼應 reconcile 紀律）。
> 狀態標記：✅ active｜⚠️ 部分過時（標明哪段）｜🪦 退役/重定向。
>
> **🪦 歷史說明 — Qdrant 已徹底廢除（2026-06-22）**：Qdrant 向量 DB + 上游 WebSocket chat 子系統已從 code 完全移除（現役檢索=PostgreSQL pgvector + pg_bigm，見 `lessons-infra-deploy`）。各 spec 的 Qdrant / source-tier 殘留已於 **2026-07-10（`/learn specs` Batch 2 全量掃）清理完畢**：active 描述改寫為現行實況，歷史脈絡段統一標「🪦 歷史紀錄（機制已於 YYYY-MM-DD 廢除）」保留；退役 spec（`kg-editing-spec.md`、`bm25-spec.md`）已移 `docs/archive/specs/`。

---

## Reasoning / Research（LR + DR 核心）

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `live-research-spec.md` | LR（Live Research Beta）六階段管線、BAB loop、grounding、publish gate、DR-parity；**最大最核心 spec（~92KB）** | ✅（2026-07-10 re-sync） |
| `reasoning-spec.md` | DR（Deep Research）Analyst/Critic/Writer、推論鏈（source tier 已廢除，殘段標 🪦，`SourceTierFilter` 為 pass-through no-op，見下方注記） | ✅（2026-07-10 re-sync） |
| `mock-bab-playbook.md` | Mock BAB 自驗法 — 解耦 evidence 蒐集（貴）vs Pipeline 處理（便宜），可重複自驗 | ✅ |
| `kg-spec.md` | Knowledge Graph 視覺化功能實作 | ✅ |
| `kg-editing-spec.md` | KG 編輯模式（已退役/重定向 2026-05-25；**檔已移 `docs/archive/specs/`，2026-07-10**。功能仍上線——現行契約見該檔頭 banner：closure factory + `POST /api/research/rerun`） | 🪦 退役（archive） |

## Ranking / Retrieval

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `xgboost-spec.md` | XGBoost ranking 實作 | ✅ |
| `bm25-spec.md` | BM25 實作（Python BM25 已退役，現行全文匹配為 pg_bigm；**檔已移 `docs/archive/specs/`，2026-07-10**） | 🪦 退役（archive） |
| `reranking-spec.md` | Reranking pipeline（Stage 1-2 Qdrant/Python-BM25 段已標 🪦 歷史；Stage 3-5 LLM/XGBoost/MMR 仍現行） | ✅（2026-07-10 清理） |
| `mmr-spec.md` | MMR（Maximal Marginal Relevance）多元性排序 | ✅ |

## Indexing / Data Pipeline

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `indexing-spec.md` | M0 Indexing 模組、來源管理、品質閘 | ✅（2026-07-10 清理：Qdrant 歷史段全標 🪦；SourceManager Tier 1-4 消歧義已補） |
| `bulk-load-spec.md` | Bulk Load Pipeline（TSV → embedding → PG） | ✅ |
| `database-spec.md` | DB schema 管理、alembic 方案 B | ✅ active |
| `code-in-sqlite.md` | 程式碼索引系統（indexer.py 的 SQLite FTS5） | ✅ |

## Crawler

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `gcp-crawler-spec.md` | GCP e2-micro crawler 部署（監控靠反向推送心跳） | ✅ |
| `laptop-crawler-spec.md` | 家用筆電 crawler 部署 | ✅ |
| `crawler-dashboard-spec.md` | Indexing Dashboard 前端規格 | ✅ |

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

## Analytics / Legal / 維運

| Spec | 涵蓋範圍 | 狀態 |
|------|---------|------|
| `analytics-spec.md` | Analytics 系統（欄名以實際 schema 為準） | ✅ |
| `legal-compliance-spec.md` | 法律合規（律師會議 2 + tl1-21 書面備忘錄的工程/產品可執行版；SoT = `memory/project_legal_consultation.md`） | ✅ active（2026-07-10 全面重寫） |
| `intern-repo-sync-spec.md` | 主 repo ↔ intern public repo 同步 playbook + strip 紀律 | ✅ |
| `everything-cc-manual.md` | Claude Code 配置使用手冊 | ✅ |

---

## ⚠️ 重要注記：source tier / 來源分級已**廢除**（非 deprecate 中）

`reasoning-spec.md`、`indexing-spec.md`、`guardrail-spec.md`、`systemmap.md` 的 source tier 段落已於 **2026-07-10 Batch 2 清理**——active 誤述已改寫、歷史段標 🪦、indexing 層 SourceTier（Tier 1-4，仍現役的另一套機制）已加消歧義。**實際狀態**（2026-06-18 拍板廢除 → Phase A + Phase B 皆已 land 上 prod，2026-07-10 稽核確認）：
- hard filtering 已於 2026-04 移除；enrichment 亦於 Phase B 移除——`reasoning/filters/source_tier.py` 的 `filter_and_enrich()` 現為**純 pass-through**（不 enrich、不 filter，items 原樣返回；Tier 6 provenance 標記由 orchestrator 獨立產生）
- 產品對外**不宣稱**來源分級（對外文案紅線見 Zoe user-level auto-memory `feedback_no_source_tier_in_external_copy.md`，**非** repo `memory/` 下——查 absence 別只查 repo memory）
- CEO 拍板**徹底廢除**（2026-06-18）。**Phase A 死碼清已 land**（2026-06-23 commit `e173b186`：source_filtering.yaml / get_tier_definitions / critic_rules tier 欄位 / mode_configs 全清）。**Phase B 亦已 land**（commit `140ffb3a`：移除 reasoning enrichment + Analyst/Critic/Writer prompt 的 Tier 1-5 語義，改 pass-through；後續 tuple regression fix `2349b6923`）——兩階段皆已上 prod。原「Phase B 待實作」的 plan 路徑 `docs/in progress/plans/source-tier-removal-plan.md` **已不存在**（工作完成後歸檔為 `docs/archive/plans/source-tier-phase-b-dr-tuple-regression-fix-plan.md`）。

派工/引用這幾個 spec 的 source tier 章節時，知道它已**廢除**（不是 deprecate 中、不是待實作），不要當 active 機制描述。
