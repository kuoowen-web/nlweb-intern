# Copyright (c) 2025 Microsoft Corporation.
# Licensed under the MIT License

"""
Helper module for retrieving user-uploaded private files during search.

This module integrates private file retrieval with the existing search pipeline.
"""

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

import yaml

from retrieval_providers.user_postgres_provider import get_user_postgres_provider
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("user_data_retriever")

# A3（票 2026-08-11-a）：私文檢索 top_k 的預設值與上限，讀 config/user_data.yaml
# 的 retrieval.default_top_k / max_top_k（兩個鍵早已存在，只是從沒被讀）。
_DEFAULT_PRIVATE_TOP_K = 10
_FALLBACK_MAX_TOP_K = 50
# module 層快取：config 檔不會在 run 中變，避免每顆 seed 讀一次 yaml。
_cached_private_top_k = None


def _resolve_user_data_config_path() -> Path:
    """定位 config/user_data.yaml（沿 webserver/middleware/upload_rate_limit.py:46-56
    的既有 pattern，不自創 config 讀法）。"""
    config_dir = os.environ.get('NLWEB_CONFIG_DIR')
    if config_dir:
        return Path(config_dir) / "user_data.yaml"
    # core/user_data_retriever.py -> repo root 是 3 層 parent（core -> python -> code -> root）
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return project_root / "config" / "user_data.yaml"


def _private_docs_top_k() -> int:
    """私文檢索的 top_k（A3）。讀 config，讀不到 fallback 10 並 warning。

    **放在本模組而非 baseHandler**：兩個 caller —— `core/baseHandler.py`（prepare
    階段）與 `reasoning/live_research/loop_engine.py`（BAB 主迴圈，Task 5）——
    都已經 import 本模組；放 baseHandler 會讓 loop_engine 反向 import handler 模組，
    引入不必要的耦合（loop_engine 目前不 import baseHandler）。

    ⚠ 不可 silent fail：讀不到 / 值不合法一律 logger.warning 後走 fallback。
    """
    global _cached_private_top_k
    if _cached_private_top_k is not None:
        return _cached_private_top_k

    top_k = _DEFAULT_PRIVATE_TOP_K
    try:
        config_path = _resolve_user_data_config_path()
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        retrieval = config.get('retrieval', {}) or {}
        value = retrieval.get('default_top_k')
        max_top_k = retrieval.get('max_top_k')
        if not isinstance(max_top_k, int) or max_top_k <= 0:
            max_top_k = _FALLBACK_MAX_TOP_K
        if isinstance(value, int) and value > 0:
            top_k = min(value, max_top_k)          # max_top_k clamp
        else:
            logger.warning(
                "[private-docs] retrieval.default_top_k missing/invalid in %s "
                "(got %r); falling back to %d", config_path, value, _DEFAULT_PRIVATE_TOP_K,
            )
    except Exception as e:
        logger.warning(
            "[private-docs] Failed to read retrieval.default_top_k from config (%s); "
            "falling back to %d", e, _DEFAULT_PRIVATE_TOP_K,
        )

    _cached_private_top_k = top_k
    return _cached_private_top_k


async def search_user_documents(
    query: str,
    user_id: str,
    top_k: int = 10,
    query_params: Optional[Dict] = None,
    org_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search user's private documents.

    Args:
        query: Search query
        user_id: User identifier
        top_k: Number of results to return
        query_params: Optional query parameters
        org_id: Organization identifier for org-level isolation (optional)

    Returns:
        List of search results from user's private files
    """
    if not user_id:
        logger.warning("No user_id provided for private document search")
        return []

    try:
        provider = get_user_postgres_provider()
        results = await provider.search_user_documents(
            query=query,
            user_id=user_id,
            top_k=top_k,
            query_params=query_params,
            org_id=org_id,
        )

        logger.info(f"Retrieved {len(results)} results from user's private documents")
        return results

    except Exception as e:
        logger.exception(f"Error searching user documents: {str(e)}")
        return []


async def merge_public_and_private_results(
    public_results: List[Dict[str, Any]],
    private_results: List[Dict[str, Any]],
    private_first: bool = True
) -> List[Dict[str, Any]]:
    """
    Merge public and private search results.

    Args:
        public_results: Results from public data sources
        private_results: Results from user's private files
        private_first: If True, private results come first

    Returns:
        Merged list of results
    """
    if private_first:
        # Private results first (higher priority)
        merged = private_results + public_results
    else:
        # Mix them (could implement more sophisticated merging strategies)
        merged = public_results + private_results

    logger.info(f"Merged results: {len(private_results)} private + {len(public_results)} public = {len(merged)} total")
    return merged


def format_private_result_for_display(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a private file search result to match the expected display format.

    Args:
        result: Raw result from UserQdrantProvider

    Returns:
        Formatted result dictionary
    """
    # The result from UserQdrantProvider already has most fields
    # This function can be used to add any additional formatting needed for display

    formatted = {
        'url': result.get('url', ''),
        # A9 / R10（票 2026-08-11-a）：原本 title 前面帶一個文件 emoji，已移除。
        # ⚠ 本註解刻意**不寫出那個 emoji 的字面**：Task 7 Step 4d 的機械檢查是
        #   `grep -rn "<emoji> 私人文件"`，預期零命中；註解裡引用舊字面會讓檢查
        #   命中自己（與 Task 2 的 pack_private_row 自我命中同型），使它從此
        #   無法分辨「真的有殘留」與「只是有人在講古」。
        # 去 emoji —— private-docs-spec 已知限制 6，私文進 LR 池後這從顯示層瑕疵
        # 升級為**匯出成品**瑕疵（title 進 references / grounding view /
        # citation tooltip / 前端 fallback 重組 / 前端來源標籤五處）。
        # ⚠ 補檔名**不在這裡做** —— 檔名要 join user_sources（chunk metadata 內
        #   確定沒有，AR R1 S6 已讀完三個 parser 確認），而本函式是同步且被三條
        #   路徑共用（自由對話 / DR / LR），在裡面打 DB 會讓另外兩條跟著付成本。
        #   LR 的補檔名在 search_private_documents_for_loop 內做（Task 5 Step 3）。
        'title': f"私人文件 (片段 {result.get('chunk_index', 0) + 1}/{result.get('total_chunks', 1)})",
        'text': result.get('content', ''),
        # 斷點 B 修補（票 2026-08-11-a）：loop_engine._normalize_item 讀的是
        # schema_obj['description'] or ['articleBody']，`text` 對不上 ⇒ snippet 恆空
        # （lessons-live-research §五同型病）。**加鍵不刪鍵**：`text` 仍被自由對話
        # 路徑（methods/generate_answer.py:358）消費，移除會直接打壞今天活著的功能。
        'description': result.get('content', ''),
        'site': '我的知識庫',
        'score': result.get('score', 0.0),
        'source_type': 'private',
        'metadata': result.get('metadata', {})
    }

    return formatted


async def search_private_documents_for_loop(
    query: str,
    user_id: str,
    top_k: int = 10,
    query_params: Optional[Dict] = None,
    org_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """BAB 迴圈專用私文檢索（票 2026-08-11-a A5）。

    與 baseHandler 那條（prepare 階段、產 4 格 row）的差異：
    本函式產 **dict**，因為 loop_engine._normalize_item 對 dict 直接放行
    （`if isinstance(item, dict): return item`），不走 4 格 row 解包。
    ⇒ dict 必須自帶 _execute_search 入池迴圈（loop_engine.py:697-700）要讀的
    url / name / title / description / snippet 五個鍵，缺一則入池後為空。

    snippet 帶 `[私人文件]` 前綴（A7 / R9）：writer prompt 的來源紀律讀的是
    snippet 前綴而非 source 欄位（reasoning/prompts/writer.py:806-807 明文
    「無 Tier 6 前綴 ⇒ 站內 corpus 真實檢索文章，可正常引用為研究/報告」），
    不帶前綴會讓使用者自己的草稿被寫成「研究指出」。
    """
    results = await search_user_documents(
        query=query, user_id=user_id, top_k=top_k,
        query_params=query_params, org_id=org_id,
    )
    if not results:
        return []

    # A9 / R10（R2 改寫，AR S6）：檔名**不在** chunk metadata 裡 —— 三個 parser
    # （pdf/text/docx）的 metadata 都不含檔名，user_data_processor 原樣掛上去。
    # 真來源是 user_sources.name，用 chunk 自帶的 source_id join。
    # 一次批次查（本次檢索的 distinct source_id 通常 1~3 個），不逐 chunk 查。
    source_ids = {r.get('source_id', '') for r in results if r.get('source_id')}
    # user_id 一併傳（R3 / AR R2 Codex nit 1）：讓「查不到別人的檔名」是 SQL 保證
    name_by_source = await _fetch_source_names(source_ids, user_id)

    out: List[Dict[str, Any]] = []
    for r in results:
        formatted = format_private_result_for_display(r)
        # A7 / R9：snippet 帶 [私人文件] 前綴（writer 來源紀律讀前綴不讀 source 欄位）
        body = f"[私人文件] {formatted['description']}"
        # A9：title 補檔名（去 emoji 已在 formatter 做掉）
        fname = name_by_source.get(r.get('source_id', ''), '')
        title = _private_doc_title(fname, r)
        out.append({
            # A16 / B2（AR Codex + agy）：url 脫敏 —— provider 產的原值是
            # private://{user_id}/{source_id}/{doc_id}，而 url 會被印進
            # final_report_markdown（_build_references_block:9797 的 url_part）、
            # APA 條目（:9854）、SSE citation（:9756）與 **writer prompt**
            # （prompts/writer.py:963 `- URL: {url}`，LLM 看得到）。
            # 只修 source_domain 不夠 —— AC-5 的主斷言查的就是報告全文。
            # ⚠ scheme 前綴 private:// **必須保留**：AC-7 的 URL 反查、
            #   DR 側 reasoning/orchestrator.py:2804 與前端 text-fragment.js:77
            #   的特判都只判前綴不判 netloc，換 netloc 安全、換 scheme 會壞。
            'url': sanitize_private_url(formatted['url']),
            'name': title,
            'title': title,
            'description': body,
            'snippet': body,
            'source_type': 'private',
        })
    return out


_PRIVATE_URL_OPAQUE_HOST = "my-knowledge-base"


def sanitize_private_url(url: str) -> str:
    """把 private://{user_id}/{source_id}/{doc_id}/{chunk_index} 的 user_id
    換成固定 opaque host（票 2026-08-14-k 起 URL 為 chunk 級四段）。

    票 2026-08-11-a A16（AR R1 B2）。保留 scheme 與 netloc 之後的**全部**路徑段：
      - scheme 保留 → 下游所有 `startswith("private://")` 特判不受影響
      - source_id / doc_id / chunk_index 保留 → 唯一標定 chunk 來源
        （_url_to_id 去重照常），未來若有反解需求也只需改「netloc 是 user_id」
        這個假設

    ⚠ **段數不可寫死**（票 2026-08-14-k 更正）：本函式原註解宣稱「保留後兩段
      路徑 ⇒ 仍能唯一標定 chunk 來源」—— 那句在當時是**假的**，因為 URL 只有
      user_id/source_id/doc_id 三段，doc_id 是每份文件一顆 uuid4，標定的是
      **文件不是 chunk**，同一份文件的 N 個 chunk 產出逐字相同的 URL 而被
      _url_to_id 併成一筆（P0，一份 22-chunk 文件只有 1/22 到得了 writer 眼前）。
      URL 現含第四段 chunk_index，那句話才成真。
      實作上 `rest.split("/", 1)[1]` 只切**第一個** `/`（分離 netloc 與其後全部），
      對任意段數都正確 —— 維護時**不要**改成 `split("/")` 再取固定 index。
    非 private:// 的 url 原樣回傳（本函式只處理私文）。

    ⚠ 拔掉 user_id 後會不會讓兩個使用者的 url 撞在一起（進而讓 _url_to_id
    去重把 A 的 chunk 當成 B 的）？**不會**，兩層理由：
      1. source_id 是 uuid4（core/user_data_manager.py:177 `str(uuid.uuid4())`），
         跨使用者碰撞機率可忽略；
      2. 更根本的是 evidence_pool 與 _url_to_id 都是 **per-request** 狀態，
         單一 request 內只會有同一個 user 的私文（gate 用 handler.user_id，
         檢索用 _build_user_docs_where 強制 WHERE user_id）——跨使用者的 url
         根本不會出現在同一個池子裡。

    ⚠ **本函式已是 evidence pool 的入池前置條件之一（票 2026-08-15-a）**，
      有兩個 caller 你改它之前必須知道：
        1. `EvidencePoolEntry._enforce_private_fields_sanitized`
           （`reasoning/schemas_live.py`）—— **型別層，也是票 2026-08-14-l 的
           實際守門人**。掛在 url / title / snippet / source_domain 四個外顯
           欄位上，涵蓋所有走 ctor / model_validate 的建 entry 路徑，含
           deserialize_evidence_pool（12 個 callsite）與
           BABLoopEngine.__init__(seed_evidence_pool=…) 的上游。
        2. `BABLoopEngine._normalize_pool_url`（`reasoning/live_research/
           loop_engine.py`）—— **engine 內入池的去重鍵定義處**。改動本函式的
           輸出 = 改動 LR evidence pool 的去重語義。
           ⚠ 它**只作用於走 _pool_put() 的三個入池點**；seed 直灌與
           deserialize 兩條路的去重鍵是用 entry.url 重建的，不經這裡。

    ⚠ **BAB 路徑上這裡的呼叫是冗餘的第二道**：本檔
      search_private_documents_for_loop 在回傳時就已呼叫本函式，
      所以 loop_engine 收到的 private url 本來就乾淨。第二道防的是
      「上游哪天被改壞」，不是「現在正在擋什麼」。

    ⚠ **已知的字面邊界（實跑 8 種形狀）**，這三種不處理：
        - `PRIVATE://…`（大寫 scheme）→ 原樣回傳
        - ` private://…`（前導空白）→ 原樣回傳
        - `private:///…`（三斜線）→ 脫敏後 uid **存活在 path 段**
      在「私文 url 只由 user_postgres_provider.py 那個 f-string 產生
      （小寫字面 scheme、user_id 是必有值的 DB 欄位）」的前提下這三種
      產不出來。**前提被打破時它們就是真洞。**
      盲區已寫成會跑的斷言：
      `tests/unit/reasoning/test_pool_write_interface_regression.py`
      的 `test_documented_pool_guard_blind_spots_still_blind`。
    """
    if not url or not url.startswith("private://"):
        return url
    rest = url[len("private://"):]
    tail = rest.split("/", 1)[1] if "/" in rest else ""
    return f"private://{_PRIVATE_URL_OPAQUE_HOST}/{tail}" if tail else (
        f"private://{_PRIVATE_URL_OPAQUE_HOST}/"
    )


def _private_doc_title(filename: str, result: Dict[str, Any]) -> str:
    """私文 title：`{檔名} (片段 N/M)`，無 emoji。

    A9 / R10。檔名取不到時 fallback「私人文件」並 warning（不可 silent fail）——
    fallback 會讓 reader 分不出來源文件，是已知降級不是正常路徑。
    """
    name = (filename or "").strip()
    if not name:
        logger.warning(
            "[private-docs] source name not found for source_id=%r; "
            "title falls back to 私人文件（reader 無法分辨來源文件）",
            result.get('source_id', ''),
        )
        name = "私人文件"
    return (
        f"{name} (片段 {result.get('chunk_index', 0) + 1}"
        f"/{result.get('total_chunks', 1)})"
    )


async def _fetch_source_names(source_ids: set, user_id: str) -> Dict[str, str]:
    """批次查 user_sources.name（A9 / R10）。查不到的鍵不放進 dict，caller 走 fallback。

    ⚠ `user_id` 是**必填**（R3 / AR R2 Codex nit 1）：source_id 雖然來自已過濾的
    檢索結果、實務上不會跨使用者，但這是私文鏈路 —— WHERE 補一個 user_id 成本為零，
    且讓「這條查詢不可能撈到別人的檔名」變成 SQL 保證而非呼叫順序保證。
    紀律同 A10：不把隔離責任推給呼叫端的正確性。

    ⚠ 不放進 format_private_result_for_display —— 那個 formatter 是同步函式且被
    三條路徑共用（自由對話 / DR / LR），在裡面打 DB 會讓另外兩條跟著付查詢成本。

    ⚠ 一次查完，不逐 chunk 查：單次檢索的 distinct source_id 通常 1~3 個，
    但 top_k 可到 50（config max_top_k）⇒ 逐 chunk 查最壞是 50 次來回。

    ⚠ 例外不可外拋：檔名只是顯示增強，查不到就 fallback「私人文件」；
    讓它炸掉會使整條私文檢索失敗（不成比例）。但**必須 log**（不可 silent fail）。
    """
    if not source_ids:
        return {}
    ids = [s for s in source_ids if s]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    try:
        # ⚠ R3 / AR R2 SF-2：`get_user_data_manager` **不是**本檔既有 import
        #（本檔 :10-12 只有 typing / get_user_postgres_provider / logger）。
        # 函式內 import：manager 會連帶拉起 storage / ParserFactory / yaml
        #（core/user_data_manager.py:24-27），而本模組被 loop_engine 與
        # baseHandler 在檔頭 import ⇒ 放檔頭等於讓每個 import 本模組的人
        # 都付那串相依成本，而檔名 join 只有 LR 這條路用得到。
        #（已實讀確認 user_data_manager **不** import 本模組，無迴圈風險，
        #  純粹是相依重量的取捨。）
        from core.user_data_manager import get_user_data_manager
        db = get_user_data_manager().db      # 沿 user_data_manager.py:389 既有取得方式
        rows = await db.fetchall(
            f"SELECT source_id, name FROM user_sources "
            f"WHERE source_id IN ({placeholders}) AND user_id = ?",
            tuple(ids) + (user_id,),
        )
    except Exception:
        logger.exception(
            "[private-docs] 批次查 user_sources.name 失敗（%d 個 source_id）；"
            "title 全部走 fallback「私人文件」", len(ids)
        )
        return {}
    return {r["source_id"]: (r.get("name") or "") for r in (rows or [])}


async def user_has_private_docs(user_id: str, org_id: Optional[str] = None) -> bool:
    """這個使用者有沒有任何私文 chunk（A14 成本短路的判準）。

    **不做 embedding**（見 provider.user_has_any_chunk 的 docstring）。
    **查 user_document_chunks，不查 user_sources**（AR R2 BL-1）——判準必須與
    實際被檢索的表同源，否則刪 source 留下的 orphan chunk 會被靜默短路掉。

    org_id 必須一路傳到底：短路判準的範圍要與 search_user_documents 的
    _build_user_docs_where 完全一致，否則會出現「判定有、實際撈不到」
    （或反之）的錯位。
    """
    if not user_id:
        return False
    try:
        provider = get_user_postgres_provider()
        return await provider.user_has_any_chunk(user_id, org_id=org_id)
    except Exception as e:
        # 不可 silent fail，但也不可因為成本最佳化的查詢失敗就讓功能整個掛掉。
        # 降級方向：**當作「有文件」**（照常查），寧可多花錢也不要漏撈私文。
        #
        # ⚠⚠ R4 / AR R3 Codex nit：這個 except 會同時吞掉**兩種性質完全不同**的失敗，
        # log 必須讓人分得出來，否則像「provider method 漏 commit」這種
        # **programming error** 會被當成「DB 暫時連不上」而永遠沒人查：
        #   (a) DB / 連線失敗（OperationalError、pool 逾時…）→ **正當的 prod 降級**，
        #       重試或環境恢復後自然好，屬可接受的暫時性成本浪費；
        #   (b) 程式接線錯誤（AttributeError：method 不存在 / 打錯名 / 簽章不符）→
        #       **不是降級，是 bug**。它的症狀是「短路永遠不生效、每個 seed 都打
        #       embedding」，而且**不會有任何紅燈**（見 Task 5 Step 8 的 git add 註解）。
        # ⇒ 兩者分開 log，(b) 額外標 WIRING BUG 讓它在 log 檢索時跳出來。
        #   **降級方向兩者相同（都 return True）—— 這裡只改可觀測性，不改行為**，
        #   因為在請求路徑上為 (b) 改丟例外會讓一個顯示層等級的最佳化打死整條檢索。
        #   (b) 的真正防線是 Step 7b 的 provider 層測試（讓它在 CI 就紅），不是這裡。
        if isinstance(e, AttributeError):
            logger.error(
                "[private-docs] WIRING BUG: provider 缺 user_has_any_chunk —— "
                "短路永久失效且無紅燈，檢查該檔是否已 commit"
            )
        logger.exception(
            "[private-docs] existence check failed; degrading to 'assume has docs' "
            "(照常檢索，成本短路本輪失效)"
        )
        return True


def pack_private_row(formatted: Dict[str, Any]) -> list:
    """把 format_private_result_for_display 的輸出打包成 4 格 retrieval row。

    票 2026-08-11-a B6（AR R1）：本 repo 原本有**三個**逐字相同的 inline 打包點
    （core/baseHandler.py:639 free_conversation、:772 標準檢索、
    methods/generate_answer.py:388 答案合成）。R1 plan 只列一處、AR 人眼只補到兩處，
    第三處是機械 grep 查出來的 —— 「記得同步 N 處」是會被第 N+1 處打敗的規則，
    故收斂成單一 source of truth。

    ⚠ 兩個鍵都要有，不可二選一：
      - 'text'        —— 自由對話路徑消費（methods/generate_answer.py:388 一帶、
                         core/baseHandler.py free_conversation 分支）
      - 'description' —— loop_engine._normalize_item:511 讀的是
                         schema_obj.get('description') or schema_obj.get('articleBody')，
                         缺這個鍵 ⇒ snippet 恆空（斷點 B，lessons-live-research §五同型病）
    """
    return [
        formatted['url'],
        json.dumps({
            'text': formatted['text'],
            'description': formatted['description'],
            'metadata': formatted.get('metadata', {}),
        }),
        formatted['title'],
        formatted['site'],
    ]
