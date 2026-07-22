"""MP-1 (full-scan 批7)：gemini_embedding 429 重試改為 bounded + async sleep 回歸測試。

修正前：`while True` + 同步 time.sleep(5) → 持續 429 永久自旋 + 阻塞 event loop。
修正後：retry_async bounded（max_retries=3）+ await asyncio.sleep + 耗盡 raise。

測試切面：mock genai client 的 embed_content（外部 API 邊界），驗 retry 行為——不打真
Gemini API。patch asyncio.sleep 避免真等 backoff。
"""

import asyncio
import sys
import types

import pytest

# google.genai 未安裝於測試 venv（gemini_embedding 為 frozen/非主路徑 provider）。
# 注入最小 stub 讓 module import 成功，才能測其 retry 邏輯（本測試不觸真 Gemini API）。
# 只在真的無法 import 時才 stub，避免覆蓋已存在的 google namespace package。
def _make_stub_module(name):
    """建帶正確 __spec__ 的 stub module（否則 importlib.util.find_spec 會拋
    'ValueError: __spec__ is None'，污染其他 test 的 import machinery）。"""
    from importlib.machinery import ModuleSpec
    mod = types.ModuleType(name)
    mod.__spec__ = ModuleSpec(name, loader=None)
    return mod


def _ensure_genai_stub():
    try:
        from google import genai  # noqa: F401
        return
    except Exception:
        pass
    _fake_genai = _make_stub_module("google.genai")
    _fake_genai.Client = lambda *a, **k: None
    _fake_types = _make_stub_module("google.genai.types")
    _fake_types.EmbedContentConfig = lambda *a, **k: object()
    _fake_genai.types = _fake_types
    if "google" in sys.modules:
        # 真 google package 已在（如 protobuf）→ 只掛上 genai 子模組
        sys.modules["google"].genai = _fake_genai
    else:
        _fake_google = _make_stub_module("google")
        _fake_google.genai = _fake_genai
        sys.modules["google"] = _fake_google
    sys.modules["google.genai"] = _fake_genai
    sys.modules["google.genai.types"] = _fake_types


_ensure_genai_stub()


@pytest.fixture
def patched_gemini(monkeypatch):
    """注入假 genai client + 短路 backoff sleep，回傳可控制 embed_content 的 handle。"""
    import embedding_providers.gemini_embedding as ge

    calls = {"n": 0}

    class _FakeEmbeddingObj:
        values = [0.1, 0.2, 0.3]

    class _FakeResult:
        embeddings = [_FakeEmbeddingObj()]

    behavior = {"mode": "ok", "raise_times": 0}

    def _embed_content(model=None, contents=None, config=None):
        calls["n"] += 1
        if behavior["mode"] == "429_always":
            raise RuntimeError("google.api_core 429 RESOURCE_EXHAUSTED quota")
        if behavior["mode"] == "429_then_ok":
            if calls["n"] <= behavior["raise_times"]:
                raise RuntimeError("429 rate limit")
            return _FakeResult()
        if behavior["mode"] == "non_429":
            raise ValueError("auth failure: invalid API key")
        return _FakeResult()

    fake_client = types.SimpleNamespace(
        models=types.SimpleNamespace(embed_content=_embed_content)
    )
    monkeypatch.setattr(ge, "get_client", lambda: fake_client)

    # 短路真實 backoff：retry_util.asyncio.sleep → 立即返回（不真等 1/2/4s）
    import core.retry_util as ru

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(ru.asyncio, "sleep", _no_sleep)

    return ge, calls, behavior


@pytest.mark.asyncio
async def test_gemini_embedding_success_no_retry(patched_gemini):
    ge, calls, behavior = patched_gemini
    behavior["mode"] = "ok"
    result = await ge.get_gemini_embeddings("hello", model="m")
    assert result == [0.1, 0.2, 0.3]
    assert calls["n"] == 1  # 一次成功不重試


@pytest.mark.asyncio
async def test_gemini_embedding_429_bounded_then_raises(patched_gemini):
    """持續 429 → 不永久自旋，max_retries=3 後（共 4 次嘗試）raise（no silent fail）。"""
    ge, calls, behavior = patched_gemini
    behavior["mode"] = "429_always"
    with pytest.raises(Exception) as exc_info:
        await asyncio.wait_for(ge.get_gemini_embeddings("hi", model="m"), timeout=3.0)
    assert "429" in str(exc_info.value)
    # 1 initial + 3 retries = 4 total attempts（bounded，不再無限）
    assert calls["n"] == 4, f"應恰好嘗試 4 次（bounded），實際 {calls['n']}"


@pytest.mark.asyncio
async def test_gemini_embedding_429_then_recovers(patched_gemini):
    """前 2 次 429 後成功 → retry 成功回傳（不炸）。"""
    ge, calls, behavior = patched_gemini
    behavior["mode"] = "429_then_ok"
    behavior["raise_times"] = 2
    result = await ge.get_gemini_embeddings("hi", model="m")
    assert result == [0.1, 0.2, 0.3]
    assert calls["n"] == 3  # 2 次失敗 + 第 3 次成功


@pytest.mark.asyncio
async def test_gemini_embedding_non_429_fails_fast(patched_gemini):
    """非 429 例外（auth 等）不重試，立即 raise（fail loud，不浪費 backoff）。"""
    ge, calls, behavior = patched_gemini
    behavior["mode"] = "non_429"
    with pytest.raises(ValueError):
        await ge.get_gemini_embeddings("hi", model="m")
    assert calls["n"] == 1, "非 429 應只嘗試 1 次（不重試）"


@pytest.mark.asyncio
async def test_gemini_batch_429_bounded(patched_gemini):
    """batch 版本亦 bounded：持續 429 在第一筆就耗盡 raise，不永久自旋。"""
    ge, calls, behavior = patched_gemini
    behavior["mode"] = "429_always"
    with pytest.raises(Exception) as exc_info:
        await asyncio.wait_for(
            ge.get_gemini_batch_embeddings(["a", "b"], model="m"), timeout=3.0
        )
    assert "429" in str(exc_info.value)
    assert calls["n"] == 4  # 第一筆 4 次嘗試後 raise，不到第二筆
