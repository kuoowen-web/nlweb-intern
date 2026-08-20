"""LR 斷線重連接管（plan: lr-reconnect-continue-takeover, 2026-08-20）。

回報症狀：即時研究進行中使用者網路斷線 → 恢復後連按三次「繼續研究」全部失敗，
斷線保護機制形同未執行。

後端真因：斷線 → detach → 舊背景 task 續跑到下個 checkpoint，期間 route 層的
per-user 並行 slot **綁在 task 終態**（spec §7.3.1 路 A，防同 session 並行雙寫），
所以使用者回來按「繼續」一律撞 `lr_user:{uid}` 429（DR_USER_LIMIT=1）。

修法：同 lr_session + 同 user 又送來新的 continue → 接管（cancel 舊 task、等它真的
結束、才放行新 continue）。不以「server 已偵測到離線」為條件——那正是最常見情境下
擋住修復的原因（見 test_stale_online_task_is_also_taken_over）。以下鎖住接管的邊界，
避免修成「無條件砍任何人的 task」這種 reward hack。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from methods import live_research as lr_mod  # noqa: E402
from methods.live_research import (  # noqa: E402
    LiveResearchHandler,
    takeover_detached_lr_task,
    _register_lr_task,
)


def _make_handler(user_id="user-1", sid="sess-takeover"):
    qp = {"query": "Q", "dry_run": "true", "session_id": sid, "user_id": user_id}
    h = LiveResearchHandler(qp, MagicMock())
    h.final_retrieved_items = []
    h.lr_session_id = sid
    h._save_state = AsyncMock()
    return h


async def _never_ends():
    await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def _clean_registry():
    lr_mod._ACTIVE_LR_TASKS.clear()
    yield
    lr_mod._ACTIVE_LR_TASKS.clear()


@pytest.mark.asyncio
async def test_no_active_task_returns_none():
    """沒有在跑的舊 task → 直接放行（正常續跑路徑不受影響）。"""
    assert await takeover_detached_lr_task("no-such-session", "user-1") == "none"


@pytest.mark.asyncio
async def test_detached_task_is_taken_over_and_cancelled():
    """舊 task 的 client 已離線 = 使用者重連回來接手 → cancel 舊 task 後放行。"""
    h = _make_handler()
    h.connection_alive_event.clear()          # client 已離線（_lr_mark_client_disconnected）
    task = asyncio.create_task(_never_ends(), name="lr_old")
    _register_lr_task(h.lr_session_id, task, h)

    result = await takeover_detached_lr_task(h.lr_session_id, "user-1")

    assert result == "taken_over"
    assert task.cancelled() or task.done(), "舊 task 必須真的結束，否則新 continue 會與它並行雙寫"
    # 接管後 registry 必須清乾淨，否則下一次 continue 會對著死 task 再接管一次
    assert h.lr_session_id not in lr_mod._ACTIVE_LR_TASKS


@pytest.mark.asyncio
async def test_stale_online_task_is_also_taken_over():
    """舊 task 仍被標成「在線」時，同 user 同 session 的新 continue 一樣要接管。

    這條是本 bug 最常見的形態：使用者網路斷掉時 server 通常還沒察覺（斷線只靠往 socket
    寫 keepalive 失敗來偵測，半開 TCP 要十幾分鐘才失敗；前面有 nginx 時更看不到）。
    若要求「已偵測到離線」才接管，使用者按「繼續」的當下舊 task 還掛著在線旗標 → 不接管
    → 照樣 429 → 等於沒修。
    """
    h = _make_handler()
    h.connection_alive_event.set()            # server 尚未偵測到斷線
    task = asyncio.create_task(_never_ends(), name="lr_stale_online")
    _register_lr_task(h.lr_session_id, task, h)

    result = await takeover_detached_lr_task(h.lr_session_id, "user-1")

    assert result == "taken_over_stale"
    assert task.cancelled() or task.done()
    # 接管前必須先把舊 handler 標成離線（成對設置），否則舊 task 收尾時還會往死連線寫
    assert not h.connection_alive_event.is_set()
    assert h._lr_detach_event.is_set()


@pytest.mark.asyncio
async def test_other_user_cannot_take_over():
    """user_id 不符 → 不接管（防猜 lr_session UUID 砍掉別人的研究）。"""
    h = _make_handler(user_id="owner")
    h.connection_alive_event.clear()
    task = asyncio.create_task(_never_ends(), name="lr_other_owner")
    _register_lr_task(h.lr_session_id, task, h)
    try:
        result = await takeover_detached_lr_task(h.lr_session_id, "attacker")
        assert result == "owner_mismatch"
        assert not task.done(), "別人的研究不可被砍"
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_timeout_does_not_release_when_old_task_refuses_to_die():
    """cancel 後逾時仍未結束 → 回 timeout（route 照常 429）。

    不可「等不到就當它死了」放行——那正是同 session 並行雙寫。
    """
    h = _make_handler()
    h.connection_alive_event.clear()

    async def _stubborn():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)   # 拖過接管 timeout
            raise

    task = asyncio.create_task(_stubborn(), name="lr_stubborn")
    await asyncio.sleep(0)
    _register_lr_task(h.lr_session_id, task, h)
    try:
        result = await takeover_detached_lr_task(h.lr_session_id, "user-1", timeout=0.05)
        assert result == "timeout"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_done_callback_unregisters_only_its_own_entry():
    """舊 task 的 done-callback 不可把接管後登記的新 task 一併撤掉。"""
    h = _make_handler()
    old_task = asyncio.create_task(_never_ends(), name="lr_old")
    _register_lr_task(h.lr_session_id, old_task, h)
    old_task.cancel()
    await asyncio.gather(old_task, return_exceptions=True)

    new_task = asyncio.create_task(_never_ends(), name="lr_new")
    _register_lr_task(h.lr_session_id, new_task, h)
    try:
        # 舊 task 的 done-callback（真實路徑）事後才跑到 → 不可刪掉新 entry
        h._on_lr_research_complete(old_task)
        assert lr_mod._ACTIVE_LR_TASKS.get(h.lr_session_id)[0] is new_task
    finally:
        new_task.cancel()
        await asyncio.gather(new_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_task_lifecycle_registers_and_unregisters(monkeypatch):
    """真實路徑：continueResearch 建 task → 登記；task 結束 → done-callback 撤登記。"""
    h = _make_handler(sid="sess-lifecycle")
    finish = asyncio.Event()

    async def _slow_continue(**kwargs):
        await finish.wait()
        return MagicMock(current_stage=2, checkpoint_prompt="cp", stage_status="checkpoint")

    fake_orch = MagicMock()
    fake_orch.continue_from_checkpoint = _slow_continue
    monkeypatch.setattr(
        "methods.live_research.LiveResearchOrchestrator", lambda **kw: fake_orch
    )
    monkeypatch.setattr(
        h, "_load_state", AsyncMock(return_value=MagicMock(schema_version=2))
    )

    cont = asyncio.create_task(h.continueResearch(user_message="go"))
    await asyncio.sleep(0.02)
    assert "sess-lifecycle" in lr_mod._ACTIVE_LR_TASKS, "背景 task 必須登記，否則重連接管找不到它"

    finish.set()
    await cont
    await asyncio.sleep(0)
    assert "sess-lifecycle" not in lr_mod._ACTIVE_LR_TASKS, "task 結束後必須撤登記，否則 registry 洩漏"
