"""單點資料衛生 helper（W-2 / D-2026-07-20 規則 4）。

背景：使用者字串含 null byte（U+0000）時 json.dumps 會序列化為跳脫
\\u0000，PostgreSQL 的 JSONB / TEXT 欄位明確拒絕 → InvalidTextRepresentation
→ route 回 500（非 fail-closed）。此漏洞散佈 session_service 所有 JSONB/TEXT
寫入點 + auth org/user name 入庫點。

根解：單點遞迴 sanitize helper，掛在所有序列化 / 入庫出口，覆蓋全族——
不逐 call site 各寫一份。
"""
from typing import Any

# PostgreSQL text/jsonb 不接受 U+0000（null byte）。這是唯一必須剝除的非法
# 字元（其餘控制字元 PG 可接受）。
_NULL_BYTE = "\x00"


def strip_null_bytes(value: Any) -> Any:
    """遞迴剝除任意結構中所有字串（含 dict key）的 U+0000 null byte。

    - str：移除所有 null byte。
    - dict：對 key 與 value 都遞迴處理。
    - list / tuple：逐元素遞迴（tuple 回傳 list，序列化語意等價）。
    - 其他 scalar（int/float/bool/None）：原樣回傳。

    只剝 null byte，不動其他字元 —— 最小介入，避免改變合法內容。
    """
    if isinstance(value, str):
        if _NULL_BYTE in value:
            return value.replace(_NULL_BYTE, "")
        return value
    if isinstance(value, dict):
        return {
            strip_null_bytes(k): strip_null_bytes(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [strip_null_bytes(v) for v in value]
    return value
