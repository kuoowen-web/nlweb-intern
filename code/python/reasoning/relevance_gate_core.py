"""證據池相關性 gate 的共用判定核心（票 2026-07-28-m）。

DR（reasoning/orchestrator.py）與 LR（reasoning/live_research/loop_engine.py）
共用同一份判定邏輯：prompt（負面表列）+ digest budget + 幻覺編號 clamp + fail-open。
兩邊各自用自己的池形狀組 digest、各自消費回傳的 irrelevant id 集合。

**gate prompt 唯一權威在此檔**——票 2026-07-29-b 將來改 prompt，DR/LR 同時受益。

fail-open 語意（與 LR hallucination_guard R1 fail-closed 反向）：🔧 R1 判定失敗一律回
None（呼叫端據此全保留、且不視為已判）+ warning——與正常「全相關 set()」語義分明；誤殺
真證據的代價高於留噪音（噪音下游還有 Analyst 判讀 + Critic + publish gate 兜底）。
"""

from typing import Any, Optional

# gate 池 digest 字數上限：超出部分呼叫端不送判、一律保留（fail-open）。
RELEVANCE_GATE_DIGEST_CHAR_BUDGET = 24000


def _build_relevance_prompt(query: str, digest: str) -> str:
    """負面表列 prompt——逐字對齊 DR gate（orchestrator.py:913-929）。

    與票 2026-07-28-f 同一份；票 2026-07-29-b 若補「同機構沾邊」規則，改此處
    DR/LR 同步生效。
    """
    return (
        "你是檢索結果相關性審核員。以下是使用者查詢與候選來源的摘要清單，"
        "請判斷哪些來源與查詢主體「完全不相關」。\n\n"
        "判定標準：\n"
        "- 「相關」= 來源內容與查詢的主體（人物 / 組織 / 事件 / 主題）有實質關聯，"
        "包括背景、上下文、產業脈絡等間接相關內容——這些都要保留。\n"
        "- 「完全不相關」= 來源講的是另一個人物 / 主題，僅因字面或模糊搜尋沾邊"
        "（例：查「邱啟新」卻回「李登輝」「彭明敏」或動漫條目的百科頁）。\n"
        "- 查詢主體是具名人物時：來源講的是**另一個具名人物**，即使同機構、同領域、"
        "同職業，也屬完全不相關——同機構不等於相關（查甲教授卻回乙教授＝不相關）。\n"
        "- **不確定就保留**（不要列入 irrelevant_ids）——誤刪真證據的代價"
        "遠高於留下噪音。\n\n"
        f"## 使用者查詢\n{query}\n\n"
        f"## 候選來源\n{digest}\n\n"
        '回傳 JSON：{"irrelevant_ids": [<完全不相關的來源編號>]}。'
        '全部相關回 {"irrelevant_ids": []}。'
    )


async def judge_irrelevant_source_ids(
    query: str,
    digest: str,
    judged_ids: list,
    query_params: dict,
    logger: Any,
    log_prefix: str = "RELEVANCE-GATE",
) -> Optional[set]:
    """批次判定 digest 中「完全不相關」的來源 id（已 clamp 到 judged_ids）。

    Args:
        query: 使用者查詢字串。
        digest: caller 已組好的候選來源摘要（多行 "[id] source - title：desc"）。
        judged_ids: 實際送判的 id list（供 clamp，防 LLM 幻覺編號）。
        query_params: 透傳給 ask_llm 的 handler query_params。
        logger: caller 的 logger（warning 走 caller 命名空間）。
        log_prefix: log tag（DR/LR 各自標識）。

    Returns:
        🔧 R1：回傳型 `Optional[set]`，三態分明——
        - `None` = **fail-open**（LLM 失敗 / LLMError sentinel / 回傳無法解析）+ warning。
          caller 據此「全保留且不視為已判」（LR：不標 judged，**同 engine 內**下輪重判；
          🔧 R3 跨 engine seed 邊界的豁免語義屬 LR caller 端裁決，見 loop_engine
          __init__ seed 預載註解——core 本身無狀態、不感知 engine 邊界）。
        - `set()` = 正常判定，**全部相關**（caller 池不動、印 info）。
        - 非空 set = 確認不相關的 id 集合（已 clamp 到 judged_ids）。

        分 `None` vs `set()` 的理由（SF1+SF2）：舊版 fail-open 也回 `set()`，與「全相關」
        無法區分 → DR 委派後 fail-open 會誤走「全相關」info 分支（log 語義漂移），且 LR
        會把 fail-open 批永久標「已判」（下輪不重判，垃圾永久豁免）。改回 `None` 兩病同治。
    """
    schema = {
        "type": "object",
        "properties": {
            "irrelevant_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["irrelevant_ids"],
    }
    from core.llm import ask_llm, LLMError

    prompt = _build_relevance_prompt(query, digest)
    try:
        resp = await ask_llm(
            prompt,
            schema,
            level="low",
            query_params=query_params or {},
            max_length=1024,
            timeout=30,
        )
    except Exception as e:
        logger.warning(
            f"[{log_prefix}] LLM failed ({type(e).__name__}: {e}); fail-open 全保留"
        )
        return None  # 🔧 R1：fail-open（caller 全保留 + 不標已判）

    if (
        isinstance(resp, LLMError)
        or not isinstance(resp, dict)
        or not isinstance(resp.get("irrelevant_ids"), list)
    ):
        logger.warning(
            f"[{log_prefix}] 回傳無法解析（fail-open 全保留）：{resp!r}"
        )
        return None  # 🔧 R1：fail-open

    # clamp：只允許剔除「實際送判」的 id（bool 是 int subclass，兩席同抓一併排除）
    # 正常判定回 set（可能為空 set = 全相關；與上方 fail-open 的 None 語義分明）。
    return {
        int(x) for x in resp["irrelevant_ids"]
        if (isinstance(x, int) and not isinstance(x, bool))
        or (isinstance(x, str) and x.isdigit())
    } & set(judged_ids)
