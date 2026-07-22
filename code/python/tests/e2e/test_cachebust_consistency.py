"""
E2E: FE-1 cachebust 一致化真環境驗證（full-scan P1 批6）。

FE-1 根因＝news-search.js 被多個 module import 但 cachebust 版本不一致 → 瀏覽器
載成兩份 module instance → `_currentResearchQueryId` 等 module-level state 分裂 →
KG rerun 找不到 query_id。修復＝所有 importer 版本一致化。

本 E2E 驗**修復的結構性不變式**（頁面上 news-search.js 引用版本唯一 = 無分裂），
不跑真 DR/KG（那會燒 LLM + 寫資料）。KG rerun 的完整功能行為由該不變式保證：
版本一致 ⇒ 單一 module instance ⇒ state 不分裂。

FE-1 的確切病灶（親驗 findings :48）＝KG 編輯 rerun 屬 **DR（深度研究報告）流程**：
`performDeepResearch`（news-search.js:309 import 的 deep-research.js instance A）寫
`_currentResearchQueryId`，`confirmKGEdit`（knowledge-graph.js:78 import 的 instance B）
讀它。修復前 A 用 715b / B 用 717a → 兩份 instance → confirmKGEdit 讀到 null →
`alert('找不到原始研究的 query_id')`，KG rerun 整條壞。修復＝全 bump 717a。

本 E2E 驗這條「同一 URL → 單一 module instance → query_id 共享」不變式（即 rerun
一定讀得到 query_id），不跑真 DR（省 LLM 錢 + 不寫 prod session）。真跑 DR 只驗
「功能 work」，而 work 由此不變式保證：分裂根消除 ⇒ confirmKGEdit 讀得到 query_id。

env：E2E_BASE_URL（預設 localhost:8000）。
"""
import re


def test_news_search_version_consistent(page, base_url):
    """頁面上所有 news-search.js 引用版本唯一（FE-1：無 module instance 分裂）。"""
    page.goto(f"{base_url}/")
    page.wait_for_selector("body", timeout=15000)

    # 抓完整 DOM 裡所有 news-search.js?v= 指紋（含動態 import wire）
    html = page.content()
    versions = re.findall(r"news-search\.js\?v=([0-9a-z]+)", html)

    assert versions, (
        "頁面找不到任何 news-search.js?v= 引用——前端結構變了或 server 未供給前端"
    )
    unique = sorted(set(versions))
    assert len(unique) == 1, (
        f"news-search.js 版本分裂（FE-1 迴歸）：發現 {len(unique)} 個不同版本 {unique}。"
        f"版本不一致 → 瀏覽器載雙 module instance → KG rerun state 分裂。"
    )


def test_deep_research_import_version_consistent_across_importers(page, base_url):
    """FE-1 確切根因：deep-research.js 跨 importer（news-search / knowledge-graph）版本唯一。

    這才是 KG rerun bug 的實際位置——不是 HTML script tag（deep-research.js 是動態
    import，HTML 掃不到），而是 news-search.js 與 knowledge-graph.js 原始碼**內部**
    import deep-research.js 的 ?v=。修復前 news-search.js:309 用 715b、
    knowledge-graph.js:78 用 717a → 兩份 instance → query_id 分裂。驗兩者一致。
    """
    page.goto(f"{base_url}/")
    page.wait_for_selector("body", timeout=15000)

    # 抓 prod 上兩個 importer 的原始碼，比對它們 import deep-research.js 的版本
    result = page.evaluate(
        """async () => {
            async function fetchSrc(p) {
                const r = await fetch(p + '?probe=' + Date.now(), {cache: 'no-store'});
                return r.ok ? await r.text() : '';
            }
            const nsSrc = await fetchSrc('/static/news-search.js');
            const kgSrc = await fetchSrc('/static/js/features/knowledge-graph.js');
            const grab = (src) => [...new Set(
                [...src.matchAll(/deep-research\\.js\\?v=([0-9a-z]+)/g)].map(m => m[1])
            )];
            // 也驗同一 URL 動態 import 回同一 instance（module 快取語義）
            const a = await import('/static/js/features/deep-research.js?v=20260717a');
            const b = await import('/static/js/features/deep-research.js?v=20260717a');
            return {
                news_search_dr_versions: grab(nsSrc),
                knowledge_graph_dr_versions: grab(kgSrc),
                same_instance_same_url: a === b
            };
        }"""
    )

    ns = result["news_search_dr_versions"]
    kg = result["knowledge_graph_dr_versions"]
    all_versions = sorted(set(ns + kg))

    assert ns, "news-search.js 內部未找到 deep-research.js import（前端結構變了）"
    assert kg, "knowledge-graph.js 內部未找到 deep-research.js import"
    assert len(all_versions) == 1, (
        f"deep-research.js 跨 importer 版本分裂（FE-1 迴歸，KG rerun bug 復發）："
        f"news-search={ns} / knowledge-graph={kg}。兩份不同版本 → 兩份 module instance → "
        f"_currentResearchQueryId 分裂 → confirmKGEdit 讀到 null → alert('找不到 query_id')。"
    )
    assert result["same_instance_same_url"], (
        "相同 URL 的 deep-research.js 動態 import 回不同 instance（module 快取語義異常）"
    )
