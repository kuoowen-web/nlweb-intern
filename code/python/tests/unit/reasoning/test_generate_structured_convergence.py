"""instructor PRIMARY path（generate_structured）收斂：
F1 = flag-ON 拆外層 asyncio.wait_for（不勒死 instructor validation retry ⊗ SDK HTTP retry）；
F4a = openai.APITimeoutError → raise TimeoutError（不落 generic Exception → 不靜默 fallback legacy）。
維持 instructor validation max_retries（schema 正確性，非 timeout 機制）。零真實 API call。"""
import asyncio
import httpx
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pydantic import BaseModel

import openai
from instructor.core import InstructorRetryException  # 親驗：頂層 `from instructor import` 會 ImportError
import reasoning.agents.base as base_mod
from reasoning.agents.base import generate_structured


class _Schema(BaseModel):
    foo: str


def _fake_cfg():
    cfg = MagicMock()
    ep = MagicMock()
    ep.api_key = "sk-test"
    ep.models.high = "gpt-5.1"
    cfg.llm_endpoints = {"openai": ep}  # 真 dict 原生有 .get（base.py:117 用 .get("openai")）
    return cfg


def _wrapped_timeout(req):
    """模擬 instructor 1.15.3 包裝後的實際例外：transport timeout 永遠被外層 try 包成
    InstructorRetryException，原始 APITimeoutError 在 __cause__（親驗 retry.py:418-532）。
    用 `raise ... from ...` 設定 __cause__。"""
    try:
        raise openai.APITimeoutError(request=req)
    except openai.APITimeoutError as cause:
        exc = InstructorRetryException(
            "timeout", n_attempts=1, total_usage=0,
        )
        exc.__cause__ = cause
        return exc


@pytest.mark.asyncio
async def test_generate_structured_flag_on_apitimeout_raises_timeouterror_not_generic():
    """flag-ON：instructor 把 transport timeout 包成 InstructorRetryException(__cause__=APITimeoutError)
    → generate_structured unwrap __cause__ → raise TimeoutError（C2 修補後的 F4a）。
    若 except 仍直接 catch 裸 APITimeoutError（修補前的 bug），這個 InstructorRetryException 會 miss
    → 落 generic Exception → caller 靜默 fallback legacy = 沒修。本 test 正是抓那個 bug。"""
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_wrapped_timeout(req))
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        with pytest.raises(TimeoutError):
            await generate_structured("p", _Schema, max_retries=3, timeout=90)


@pytest.mark.asyncio
async def test_generate_structured_flag_on_uses_generous_outer_deadline():
    """flag-ON（should-fix 1，AR round-3）：包一個**極寬鬆**的最外層 asyncio.wait_for 當終極死線，
    deadline = timeout + buffer（見 _outer_deadline）。buffer 給足讓 C1 的正常 instructor
    validation×SDK retry 不被砍，但補回 F1 拆掉的絕對 wall-clock 上限（防 stop_after_delay 不搶占
    in-flight 造成的 ~2×timeout 膨脹無限飄）。

    驗：(a) flag-ON 確實過 asyncio.wait_for（不再裸呼）、(b) 傳給 wait_for 的 timeout 是寬鬆
    deadline（> 原 timeout，不是緊掐 timeout）、(c) 正常完成的 create 結果照常回傳。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_Schema(foo="ok"))
    captured = {}
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(awaitable, timeout):
        captured["timeout"] = timeout
        return await real_wait_for(awaitable, timeout)

    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True), \
         patch("reasoning.agents.base.asyncio.wait_for", new=_spy_wait_for):
        result, _, _ = await generate_structured("p", _Schema, max_retries=3, timeout=90)
    assert result.foo == "ok"
    assert "timeout" in captured, "flag-ON 應過外層 asyncio.wait_for（終極死線）"
    # 寬鬆 deadline：必須嚴格大於原 timeout（不可緊掐），且 == _outer_deadline(90)。
    assert captured["timeout"] == base_mod._outer_deadline(90)
    assert captured["timeout"] > 90, "buffer 必須給足（嚴格大於 timeout），不可砍正常 retry"


@pytest.mark.asyncio
async def test_outer_deadline_buffer_covers_inflation():
    """should-fix 1：_outer_deadline 的 buffer 必須覆蓋合理膨脹（stop_after_delay 不搶占 in-flight
    → 最壞 ~2×timeout）。斷言 deadline >= 2×timeout（嚴格大於，留 backoff/overhead margin），
    確保正常 retry 鏈不被外層死線砍（stop-and-report 條件 5）。"""
    for t in (90, 120, 300):
        d = base_mod._outer_deadline(t)
        assert d > 2 * t, f"timeout={t}: deadline={d} 必須 > 2×timeout 以不砍正常 retry"


@pytest.mark.asyncio
async def test_generate_structured_flag_on_outer_deadline_fires_on_runaway():
    """should-fix 1：若 instructor 真的卡到超過寬鬆 deadline（runaway，C1 機制全失效的極端情況），
    外層 asyncio.wait_for 仍 fire → raise TimeoutError（絕對死線兜底）。"""
    async def _runaway(*a, **k):
        await asyncio.sleep(10)  # 遠超下面用的 tiny deadline
        return _Schema(foo="late")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_runaway)
    # 用極小 timeout 讓 _outer_deadline 也小（2×0.01+30 級但實際 sleep 10s 仍 < deadline?）→
    # 改 patch _outer_deadline 回傳 tiny 值，純驗外層死線會 fire。
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True), \
         patch.object(base_mod, "_outer_deadline", return_value=0.05):
        with pytest.raises(TimeoutError):
            await generate_structured("p", _Schema, max_retries=3, timeout=90)


@pytest.mark.asyncio
async def test_generate_structured_flag_on_keeps_validation_max_retries():
    """flag-ON：instructor validation max_retries 仍傳給 create（schema 正確性，不該拆）。
    並驗 C1：create() 必須收到 timeout=（instructor 內部裝 stop_after_delay，wall-clock 天花板）。"""
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_Schema(foo="ok"))
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=True):
        await generate_structured("p", _Schema, max_retries=3, timeout=90)
    _, kwargs = fake_client.chat.completions.create.call_args
    assert kwargs["max_retries"] == 3, "instructor validation retry 必須維持"
    assert kwargs["timeout"] == 90, "C1：flag-ON 必須補傳 timeout=（否則 instructor 無 wall-clock 天花板）"


@pytest.mark.asyncio
async def test_generate_structured_flag_off_unchanged_outer_wait_for():
    """flag-OFF：維持外層 asyncio.wait_for(timeout) 舊路徑（asyncio.TimeoutError → raise TimeoutError）。"""
    async def _hang(*a, **k):
        await asyncio.sleep(10)
        return _Schema(foo="late")
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=_hang)
    with patch.object(base_mod, "CONFIG", _fake_cfg()), \
         patch.object(base_mod, "_get_instructor_client", AsyncMock(return_value=fake_client)), \
         patch("reasoning.agents.base.keepalive_timeout_enabled", return_value=False):
        with pytest.raises(TimeoutError):
            await generate_structured("p", _Schema, max_retries=3, timeout=1)  # 1s 外層 wait_for fire
