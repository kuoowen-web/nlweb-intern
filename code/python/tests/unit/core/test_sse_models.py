import json
import os
import pytest
from core.sse.models import BeginNlwebResponse, SseEnvelope, parse_sse_envelope

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "sse")


def _load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def test_begin_model_parses_real_fixture():
    data = _load("begin_nlweb_response.wire.json")
    m = BeginNlwebResponse.model_validate(data)
    assert m.message_type == "begin-nlweb-response"
    assert m.conversation_id == "conv-abc"
    assert m.query_id == "q-123"


def test_begin_model_roundtrip_preserves_wire_shape():
    # 鐵律驗證：序列化回去的 dict 必須與現行 wire 形狀等價（by_alias, exclude_none）
    data = _load("begin_nlweb_response.wire.json")
    m = BeginNlwebResponse.model_validate(data)
    out = m.model_dump(by_alias=True, exclude_none=True)
    assert out == data


def test_begin_fixture_does_not_fall_to_base(  # 🔧 B4：live fixture 不可落 base
):
    data = _load("begin_nlweb_response.wire.json")
    resolved = parse_sse_envelope(data)
    assert type(resolved) is not SseEnvelope, (
        "begin 落到 base SseEnvelope — 模型漏註冊，extra=allow 會假綠"
    )


import glob  # noqa: E402

# 只掃 .wire.json（前後端共讀的 wire 真相；.source.json 另有 source 專測）
WIRE = glob.glob(os.path.join(FIX, "*.wire.json"))


@pytest.mark.parametrize("path", WIRE)
def test_every_wire_fixture_roundtrips(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    m = parse_sse_envelope(data)
    out = m.model_dump(by_alias=True, exclude_none=True)
    assert out == data, f"roundtrip drift for {os.path.basename(path)}"


# 🔧 B4 牙 1：防 extra='allow' 兜底假綠——live wire fixture 不可落 base SseEnvelope。
# 若某 message_type 漏註冊進 _REGISTRY，parse 回 base，此 assert 立刻紅。
@pytest.mark.parametrize("path", WIRE)
def test_every_wire_fixture_resolves_to_specific_model(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    resolved = parse_sse_envelope(data)
    assert type(resolved) is not SseEnvelope, (
        f"{os.path.basename(path)} ({data.get('message_type')!r}) 落到 base "
        f"SseEnvelope — 模型漏建/漏註冊，extra=allow 假綠。每個 live event 必須有專屬 model。"
    )


# 🔧 B4 牙 2：registry coverage——§0.6 live 清單每個 message_type 都在 _REGISTRY 有專屬 class。
# 依賴 core.sse.registry.LIVE_MODEL_TYPES（Task 8 Step 5 建）。Task 8 已 land registry.py →
# 轉為**必綠**（移除 importorskip：registry 缺席時此 test 應 hard-fail，證明它真的在跑）。
def test_registry_covers_all_live_message_types():
    from core.sse.models import _REGISTRY
    from core.sse.registry import LIVE_MODEL_TYPES
    missing = LIVE_MODEL_TYPES - set(_REGISTRY.keys())
    assert not missing, f"live message_types 未註冊進 _REGISTRY（會落 base 假綠）：{sorted(missing)}"
    # 反向：_REGISTRY 不該含非 live 清單的殭屍註冊
    extra = set(_REGISTRY.keys()) - LIVE_MODEL_TYPES
    assert not extra, f"_REGISTRY 含 LIVE 清單外的 message_type（清單/註冊漂移）：{sorted(extra)}"


# 🔧R3（R2-BLK-3 第二半 + SF2）strict-parser dev-raise test + senderInfo fixture test
def test_typed_validate_strict_raises_on_unregistered_live_type(monkeypatch):
    # 🔧R3（R2-BLK-3）：message_type ∈ LIVE_MODEL_TYPES 卻漏註冊（落 base）→ dev/CI raise。
    # 依賴 registry.py 的 LIVE_MODEL_TYPES（Task 8 已 land）→ 轉為**必綠**（移除 importorskip）。
    from core.sse import send as send_mod
    from core.sse.registry import LIVE_MODEL_TYPES
    monkeypatch.setattr(send_mod, "TYPED_VALIDATE_ON", True)
    monkeypatch.setattr(send_mod, "_IS_PROD", False)
    victim = next(iter(LIVE_MODEL_TYPES))
    from core.sse import models
    saved = models._REGISTRY.pop(victim)
    try:
        # 🔧R5（R4-SF-2）：斷言**精確型別** send_mod.SseTypedValidationError，非寬 Exception。
        with pytest.raises(send_mod.SseTypedValidationError):
            send_mod._typed_validate({"message_type": victim})
    finally:
        models._REGISTRY[victim] = saved


def test_system_sender_camel_senderInfo_roundtrips():
    # 🔧R3（SF2）：system sender 的 camelCase senderInfo 不靠 extra 吞，模型建模、by_alias round-trip。
    data = {"message_type": "intermediate_result", "stage": "s",
            "senderInfo": {"id": "system", "name": "NLWeb"},
            "timestamp": 1721000000000, "message_id": "h#1", "conversation_id": "c", "user_id": "u"}
    m = parse_sse_envelope(data)
    assert type(m) is not SseEnvelope
    assert m.model_dump(by_alias=True, exclude_none=True) == data  # senderInfo 逐字保留
