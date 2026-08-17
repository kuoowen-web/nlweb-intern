"""跨模組共用的 fail-loud enum normalize（plan: cross-module-contract-hardening, Task 3）。

**設計沿 `reasoning/relevance_gate_core.py:45-117` 的三態形狀** —— 全 repo 唯一
把「查無」與「壞掉」分開的地方，本檔把該形狀推廣成通用原語。

核心規則：**未知值一律回 None + ERROR log，絕不回一個「最寬鬆的預設值」。**
理由（真實事故）：LR critic status 兩處 normalize 把未知值 fallback 成 "PASS"，
LLM 吐 "REJECTED" → 不在白名單 → 當 PASS → REJECT claim 入庫標 PASS + KG 照
merge + writer 照用，全程零 ERROR log。fallback 到最寬鬆分支 = 錯誤靜默放行。
"""
import logging
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


def normalize_enum(
    value: Any,
    allowed: Sequence[str],
    *,
    field: str,
    log: Optional[logging.Logger] = None,
) -> Optional[str]:
    """把外部（LLM / DB / 前端）來的值正規化成 allowed 之一。

    Args:
        value: 待正規化的值。
        allowed: 合法值（比對時大小寫不敏感，回傳一律用 allowed 裡的原形）。
        field: 欄位名 —— **必填 keyword**，log 靠它定位（本函式全 repo 共用）。
        log: caller 的 logger（讓 ERROR 落在 caller 命名空間便於追）。

    Returns:
        - allowed 中的值：正常。
        - `None`：**未知或缺值**。caller 必須自行決定處置，
          且**不得**把 None 當成「最寬鬆的那一個」。

        「查無」（value 為 None/空）與「壞掉」（value 有值但不在 allowed）
        都回 None，但 **log 訊息可區分** —— 兩者的 caller 處置常不同
        （查無可能是正常的 optional 欄位；壞掉一定是 contract 違反）。
    """
    lg = log or logger
    if value is None or (isinstance(value, str) and not value.strip()):
        lg.error(
            "[CONTRACT] enum field=%s missing (value=%r); caller must decide "
            "explicitly — no permissive default is applied here.",
            field, value,
        )
        return None
    if not isinstance(value, str):
        lg.error(
            "[CONTRACT] enum field=%s got non-string %s(%r); treated as unknown.",
            field, type(value).__name__, value,
        )
        return None
    canon = {a.upper(): a for a in allowed}
    hit = canon.get(value.strip().upper())
    if hit is None:
        lg.error(
            "[CONTRACT] enum field=%s unknown value %r (allowed=%s); "
            "returning None — caller must decide explicitly. "
            "A permissive fallback here would silently pass bad data downstream.",
            field, value, list(allowed),
        )
    return hit
