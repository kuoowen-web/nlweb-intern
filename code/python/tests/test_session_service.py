"""
Tests for core/session_service.py — session CRUD, JSONB append, soft delete, preferences.

Uses real SQLite via AuthDB (no mocks). Each test gets a fresh database.
"""

import os
import json
import time
import uuid

import pytest
import pytest_asyncio

from auth.auth_db import AuthDB
from core.session_service import SessionService, JSONB_SIZE_WARNING_THRESHOLD

# Force SQLite mode: pop AFTER imports (load_dotenv in logger.py re-sets them)
os.environ.pop('DATABASE_URL', None)
os.environ.pop('ANALYTICS_DATABASE_URL', None)
os.environ.pop('POSTGRES_CONNECTION_STRING', None)


# ── Fixtures ──────────────────────────────────────────────────────


USER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """Reset AuthDB singleton with a fresh SQLite for each test."""
    db_path = str(tmp_path / "session_test.db")

    AuthDB._instance = None
    db = AuthDB(db_path=db_path)
    AuthDB._instance = db
    db._init_database_sync()
    db._initialized = True

    # Insert a user and org so FK constraints are satisfied
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    now = time.time()
    conn.execute(
        "INSERT INTO organizations (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
        (ORG_ID, "Test Org", "test-org", now)
    )
    conn.execute(
        "INSERT INTO users (id, email, password_hash, name, created_at) VALUES (?, ?, ?, ?, ?)",
        (USER_ID, "sess@test.com", "fakehash", "Session Tester", now)
    )
    conn.commit()
    conn.close()

    yield db

    AuthDB._instance = None


@pytest.fixture
def svc():
    return SessionService()


# ── Create Session Tests ─────────────────────────────────────────


class TestCreateSession:

    @pytest.mark.asyncio
    async def test_create_returns_id_and_timestamps(self, svc):
        result = await svc.create_session(USER_ID, ORG_ID, title="My Search")
        assert 'id' in result
        assert result['title'] == "My Search"
        assert 'created_at' in result
        assert 'updated_at' in result
        assert result['created_at'] == result['updated_at']

    @pytest.mark.asyncio
    async def test_create_with_initial_data(self, svc):
        history = [{"role": "user", "content": "hello"}]
        result = await svc.create_session(
            USER_ID, ORG_ID,
            title="With Data",
            conversation_history=history,
        )
        # Fetch back and verify
        session = await svc.get_session(result['id'], USER_ID, ORG_ID)
        assert session is not None
        assert session['conversation_history'] == history

    @pytest.mark.asyncio
    async def test_create_without_title(self, svc):
        result = await svc.create_session(USER_ID, ORG_ID)
        assert result['title'] is None


# ── Get Session Tests ────────────────────────────────────────────


class TestGetSession:

    @pytest.mark.asyncio
    async def test_get_existing_session(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Find Me")
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session is not None
        assert session['id'] == created['id']
        assert session['title'] == "Find Me"

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, svc):
        session = await svc.get_session("nonexistent-id", USER_ID, ORG_ID)
        assert session is None

    @pytest.mark.asyncio
    async def test_get_wrong_user_returns_none(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Private")
        session = await svc.get_session(created['id'], "other-user-id", ORG_ID)
        assert session is None

    @pytest.mark.asyncio
    async def test_get_deserializes_json_fields(self, svc):
        articles = [{"url": "http://a.com", "title": "A"}]
        report = {"summary": "test report"}
        created = await svc.create_session(
            USER_ID, ORG_ID,
            accumulated_articles=articles,
            research_report=report,
        )
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert isinstance(session['accumulated_articles'], list)
        assert session['accumulated_articles'] == articles
        assert isinstance(session['research_report'], dict)
        assert session['research_report'] == report

    @pytest.mark.asyncio
    async def test_get_session_with_analytics_id_returns_none_not_exception(self, svc):
        """LR #19 防呆：非 UUID shaped session_id（如 analytics id sess_xxx）
        應 return None 而非拋出 InvalidTextRepresentation（PG UUID 欄位型別錯誤）。
        SQLite fixture 中 id 欄位是 TEXT，容忍任意字串不拋例外 — 這個 test
        在 SQLite 下會因為根本查不到而 return None（pass），但仍驗證介面契約。
        真正的防呆（_is_uuid_shaped guard）保護 PostgreSQL 路徑（B1 fix）。
        """
        # Pass an analytics-style session_id (not a UUID)
        result = await svc.get_session("sess_1465fb2464d3", USER_ID, ORG_ID)
        # Must return None, never raise an exception
        assert result is None

    @pytest.mark.asyncio
    async def test_get_session_shared_with_analytics_id_returns_none_not_exception(self, svc):
        """LR #19 防呆：get_session_shared 的同等保護。"""
        result = await svc.get_session_shared("sess_1465fb2464d3", USER_ID, ORG_ID)
        assert result is None


# ── List Sessions Tests ──────────────────────────────────────────


class TestListSessions:

    @pytest.mark.asyncio
    async def test_list_returns_user_sessions(self, svc):
        await svc.create_session(USER_ID, ORG_ID, title="S1")
        await svc.create_session(USER_ID, ORG_ID, title="S2")
        sessions = await svc.list_sessions(USER_ID, ORG_ID)
        assert len(sessions) == 2
        titles = {s['title'] for s in sessions}
        assert titles == {"S1", "S2"}

    @pytest.mark.asyncio
    async def test_list_excludes_deleted(self, svc):
        s1 = await svc.create_session(USER_ID, ORG_ID, title="Active")
        s2 = await svc.create_session(USER_ID, ORG_ID, title="Deleted")
        await svc.delete_session(s2['id'], USER_ID, ORG_ID)
        sessions = await svc.list_sessions(USER_ID, ORG_ID)
        assert len(sessions) == 1
        assert sessions[0]['title'] == "Active"

    @pytest.mark.asyncio
    async def test_list_respects_limit(self, svc):
        for i in range(5):
            await svc.create_session(USER_ID, ORG_ID, title=f"S{i}")
        sessions = await svc.list_sessions(USER_ID, ORG_ID, limit=3)
        assert len(sessions) == 3

    @pytest.mark.asyncio
    async def test_list_empty_for_other_user(self, svc):
        await svc.create_session(USER_ID, ORG_ID, title="Mine")
        sessions = await svc.list_sessions("other-user", ORG_ID)
        assert len(sessions) == 0


# ── Update Session Tests ────────────────────────────────────────


class TestUpdateSession:

    @pytest.mark.asyncio
    async def test_update_title(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Original")
        result = await svc.update_session(created['id'], USER_ID, ORG_ID, {'title': 'Updated'})
        assert result is True

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session['title'] == "Updated"

    @pytest.mark.asyncio
    async def test_update_conversation_history(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID)
        new_history = [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]
        await svc.update_session(created['id'], USER_ID, ORG_ID, {'conversation_history': new_history})

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session['conversation_history'] == new_history

    @pytest.mark.asyncio
    async def test_update_disallowed_field_ignored(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="NoChange")
        result = await svc.update_session(created['id'], USER_ID, ORG_ID, {'id': 'hacked'})
        assert result is False  # No allowed fields -> returns False

    @pytest.mark.asyncio
    async def test_update_bumps_updated_at(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Time")
        original_time = created['updated_at']

        # Small delay to ensure timestamp difference
        import asyncio
        await asyncio.sleep(0.05)

        await svc.update_session(created['id'], USER_ID, ORG_ID, {'title': 'Time2'})
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session['updated_at'] > original_time

    @pytest.mark.asyncio
    async def test_update_is_archived(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Archive Me")
        await svc.update_session(created['id'], USER_ID, ORG_ID, {'is_archived': True})

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session['is_archived'] is True


# ── Soft Delete Tests ───────────────────────────────────────────


class TestSoftDelete:

    @pytest.mark.asyncio
    async def test_delete_sets_deleted_at(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Delete Me")
        result = await svc.delete_session(created['id'], USER_ID, ORG_ID)
        assert result is True

        # Should not be retrievable
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session is None

    @pytest.mark.asyncio
    async def test_delete_is_soft(self, svc):
        """Soft-deleted sessions still exist in DB, just have deleted_at set."""
        created = await svc.create_session(USER_ID, ORG_ID, title="Soft")
        await svc.delete_session(created['id'], USER_ID, ORG_ID)

        db = AuthDB.get_instance()
        row = await db.fetchone(
            "SELECT deleted_at FROM search_sessions WHERE id = ?", (created['id'],)
        )
        assert row is not None
        assert row['deleted_at'] is not None

    @pytest.mark.asyncio
    async def test_restore_session(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Restore Me")
        await svc.delete_session(created['id'], USER_ID, ORG_ID)

        # Should not be visible
        assert await svc.get_session(created['id'], USER_ID, ORG_ID) is None

        # Restore it
        await svc.restore_session(created['id'], USER_ID, ORG_ID)

        # Should be visible again
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session is not None
        assert session['title'] == "Restore Me"

    @pytest.mark.asyncio
    async def test_restore_expired_raises(self, svc):
        """Sessions deleted more than 30 days ago cannot be restored."""
        created = await svc.create_session(USER_ID, ORG_ID, title="Old")
        db = AuthDB.get_instance()
        # Set deleted_at to 31 days ago
        old_time = time.time() - 31 * 24 * 3600
        await db.execute(
            "UPDATE search_sessions SET deleted_at = ? WHERE id = ?",
            (old_time, created['id'])
        )
        with pytest.raises(ValueError, match="past 30-day"):
            await svc.restore_session(created['id'], USER_ID, ORG_ID)


# ── Get Expired Deleted Sessions Tests ──────────────────────────


class TestExpiredDeletedSessions:

    @pytest.mark.asyncio
    async def test_returns_old_deleted_sessions(self, svc):
        s1 = await svc.create_session(USER_ID, ORG_ID, title="Old Deleted")
        s2 = await svc.create_session(USER_ID, ORG_ID, title="Recent Deleted")

        db = AuthDB.get_instance()
        # get_expired_deleted_sessions uses datetime objects as query params, so we must
        # store deleted_at as ISO datetime strings (not Unix float timestamps) for
        # the SQLite comparison to work correctly.
        from datetime import datetime, timedelta, timezone
        old_dt = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        recent_dt = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()

        # Mark s1 as deleted 45 days ago (older than 30-day threshold)
        await db.execute(
            "UPDATE search_sessions SET deleted_at = ? WHERE id = ?",
            (old_dt, s1['id'])
        )
        # Mark s2 as deleted 5 days ago (within 30-day threshold, should NOT be expired)
        await db.execute(
            "UPDATE search_sessions SET deleted_at = ? WHERE id = ?",
            (recent_dt, s2['id'])
        )

        expired = await svc.get_expired_deleted_sessions(days=30)
        ids = {s['id'] for s in expired}
        assert s1['id'] in ids
        assert s2['id'] not in ids

    @pytest.mark.asyncio
    async def test_active_sessions_not_returned(self, svc):
        await svc.create_session(USER_ID, ORG_ID, title="Active")
        expired = await svc.get_expired_deleted_sessions()
        assert len(expired) == 0


# ── JSONB Append Tests ──────────────────────────────────────────


class TestAppendMessage:

    @pytest.mark.asyncio
    async def test_append_message(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID)
        msg = {"role": "user", "content": "hello"}
        result = await svc.append_message(created['id'], USER_ID, ORG_ID, msg)
        assert result is True

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert len(session['conversation_history']) == 1
        assert session['conversation_history'][0] == msg

    @pytest.mark.asyncio
    async def test_append_multiple_messages(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID)
        for i in range(3):
            await svc.append_message(created['id'], USER_ID, ORG_ID, {"role": "user", "content": f"msg {i}"})

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert len(session['conversation_history']) == 3

    @pytest.mark.asyncio
    async def test_append_to_nonexistent_session(self, svc):
        result = await svc.append_message("nonexistent", USER_ID, ORG_ID, {"content": "x"})
        assert result is False


class TestAppendArticles:

    @pytest.mark.asyncio
    async def test_append_articles(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID)
        articles = [{"url": "http://a.com", "title": "A"}, {"url": "http://b.com", "title": "B"}]
        result = await svc.append_articles(created['id'], USER_ID, ORG_ID, articles)
        assert result is True

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert len(session['accumulated_articles']) == 2

    @pytest.mark.asyncio
    async def test_append_articles_accumulates(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID)
        await svc.append_articles(created['id'], USER_ID, ORG_ID, [{"url": "http://a.com"}])
        await svc.append_articles(created['id'], USER_ID, ORG_ID, [{"url": "http://b.com"}])

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert len(session['accumulated_articles']) == 2


# ── Permanent Delete Tests ──────────────────────────────────────


class TestPermanentDelete:

    @pytest.mark.asyncio
    async def test_permanent_delete_removes_from_db(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Gone")
        await svc.permanent_delete(created['id'])

        db = AuthDB.get_instance()
        row = await db.fetchone("SELECT id FROM search_sessions WHERE id = ?", (created['id'],))
        assert row is None


# ── Preferences Tests ────────────────────────────────────────────


class TestPreferences:

    @pytest.mark.asyncio
    async def test_set_and_get_preference(self, svc):
        await svc.set_preference(USER_ID, ORG_ID, "theme", "dark")
        prefs = await svc.get_preferences(USER_ID, ORG_ID)
        assert prefs["theme"] == "dark"

    @pytest.mark.asyncio
    async def test_set_preference_overwrite(self, svc):
        await svc.set_preference(USER_ID, ORG_ID, "lang", "en")
        await svc.set_preference(USER_ID, ORG_ID, "lang", "zh")
        prefs = await svc.get_preferences(USER_ID, ORG_ID)
        assert prefs["lang"] == "zh"

    @pytest.mark.asyncio
    async def test_get_preferences_empty(self, svc):
        prefs = await svc.get_preferences(USER_ID, ORG_ID)
        assert prefs == {}

    @pytest.mark.asyncio
    async def test_complex_preference_value(self, svc):
        value = {"sources": ["cna", "ltn"], "maxResults": 20}
        await svc.set_preference(USER_ID, ORG_ID, "search_config", value)
        prefs = await svc.get_preferences(USER_ID, ORG_ID)
        assert prefs["search_config"] == value


# ── Visibility Tests ────────────────────────────────────────────


class TestVisibility:

    @pytest.mark.asyncio
    async def test_set_visibility_valid(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Shared")
        result = await svc.set_visibility(created['id'], USER_ID, ORG_ID, 'team')
        assert result is True

        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session['visibility'] == 'team'

    @pytest.mark.asyncio
    async def test_set_visibility_invalid(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Bad Vis")
        with pytest.raises(ValueError, match="visibility must be one of"):
            await svc.set_visibility(created['id'], USER_ID, ORG_ID, 'public')

    @pytest.mark.asyncio
    async def test_set_visibility_nonexistent_session(self, svc):
        with pytest.raises(ValueError, match="not found"):
            await svc.set_visibility("nonexistent", USER_ID, ORG_ID, 'team')


# ── Org ID Isolation Tests ───────────────────────────────────────


OTHER_ORG_ID = str(uuid.uuid4())


class TestOrgIsolation:
    """Verify that session operations enforce org_id boundaries (multi-org data isolation)."""

    @pytest.mark.asyncio
    async def test_get_session_wrong_org_returns_none(self, svc):
        """A session created in ORG_ID must not be accessible with a different org_id."""
        created = await svc.create_session(USER_ID, ORG_ID, title="Org A Secret")
        # Attempt access with a different org_id
        result = await svc.get_session(created['id'], USER_ID, OTHER_ORG_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sessions_excludes_other_org(self, svc):
        """list_sessions with a different org_id must not return sessions from ORG_ID."""
        await svc.create_session(USER_ID, ORG_ID, title="Org A Session")
        # Same user_id but different org
        sessions = await svc.list_sessions(USER_ID, OTHER_ORG_ID)
        assert len(sessions) == 0

    @pytest.mark.asyncio
    async def test_update_session_wrong_org_no_effect(self, svc):
        """update_session with wrong org_id must not modify the session."""
        created = await svc.create_session(USER_ID, ORG_ID, title="Original Title")
        # Try to update using a different org_id
        await svc.update_session(created['id'], USER_ID, OTHER_ORG_ID, {'title': 'Hacked Title'})
        # Original session must be unchanged
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session is not None
        assert session['title'] == "Original Title"

    @pytest.mark.asyncio
    async def test_delete_session_wrong_org_no_effect(self, svc):
        """delete_session with wrong org_id must not soft-delete the session."""
        created = await svc.create_session(USER_ID, ORG_ID, title="Keep Me")
        # Attempt delete with wrong org
        await svc.delete_session(created['id'], USER_ID, OTHER_ORG_ID)
        # Session must still be accessible under the correct org
        session = await svc.get_session(created['id'], USER_ID, ORG_ID)
        assert session is not None
        assert session['title'] == "Keep Me"


# ── Export Tests ────────────────────────────────────────────────


class TestExport:

    @pytest.mark.asyncio
    async def test_export_json(self, svc):
        articles = [{"url": "http://x.com", "title": "X", "source": "Src"}]
        created = await svc.create_session(
            USER_ID, ORG_ID, title="Export",
            accumulated_articles=articles,
        )
        result = await svc.export_session(created['id'], USER_ID, ORG_ID, format='json')
        assert result['title'] == "Export"
        assert result['accumulated_articles'] == articles

    @pytest.mark.asyncio
    async def test_export_citations(self, svc):
        articles = [{"url": "http://x.com", "title": "Article X", "source": "Source A", "published_date": "2026-01-01"}]
        created = await svc.create_session(
            USER_ID, ORG_ID, title="Citations",
            accumulated_articles=articles,
        )
        result = await svc.export_session(created['id'], USER_ID, ORG_ID, format='citations')
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Article X" in result[0]
        assert "Source A" in result[0]

    @pytest.mark.asyncio
    async def test_export_nonexistent_session(self, svc):
        with pytest.raises(ValueError, match="Session not found"):
            await svc.export_session("nonexistent", USER_ID, ORG_ID)

    @pytest.mark.asyncio
    async def test_export_unsupported_format(self, svc):
        created = await svc.create_session(USER_ID, ORG_ID, title="Bad Format")
        with pytest.raises(ValueError, match="Unsupported export format"):
            await svc.export_session(created['id'], USER_ID, ORG_ID, format='pdf')


class TestLRDialogSnapshot:
    """LR dialog DOM snapshot — isolated top-level column (plan v3 Task 1)."""

    @pytest.mark.asyncio
    async def test_lr_dialog_snapshot_roundtrip_and_isolation(self, svc):
        sid = (await svc.create_session(USER_ID, ORG_ID, title="S1"))['id']
        # 模擬後端 _save_state 寫 per-stage state
        await svc.update_session(sid, USER_ID, ORG_ID,
            {'live_research_state': {'current_stage': 6, 'context_map_json': '{"k":1}'}})
        # 前端存 snapshot（獨立欄位）
        snap = [{'type': 'narration', 'stage': 1, 'html': '<p>x</p>', 'dataset': {}, 'ts': 1}]
        await svc.update_session(sid, USER_ID, ORG_ID, {'lr_dialog_snapshot': snap})
        row = await svc.get_session(sid, USER_ID, ORG_ID)
        lrs = row['live_research_state']
        if isinstance(lrs, str):
            lrs = json.loads(lrs)
        snap_out = row['lr_dialog_snapshot']
        # 【N1 — 禁容錯掩蓋】deserialize 必須回 list（jsonb_fields 白名單已加 lr_dialog_snapshot）。
        # 絕不可寫 `if isinstance(str): json.loads` 幫它容錯 —— 那會在 N1 漏修（SQLite 回字串）時假綠燈。
        assert isinstance(snap_out, list), \
            f"deserialize 必回 list 非字串(N1 jsonb_fields 漏加?) got {type(snap_out)}"
        assert lrs['current_stage'] == 6              # live_research_state 未被 snapshot 覆蓋
        assert snap_out == snap                        # snapshot 寫入且讀回
        # 反向：後端再寫一次 state，不應抹掉 snapshot
        await svc.update_session(sid, USER_ID, ORG_ID,
            {'live_research_state': {'current_stage': 6, 'context_map_json': '{"k":2}'}})
        row2 = await svc.get_session(sid, USER_ID, ORG_ID)
        snap2 = row2['lr_dialog_snapshot']
        assert isinstance(snap2, list)                 # 同上，強斷言不容錯
        assert snap2 == snap                           # snapshot 仍在（C1 根治證明）

    @pytest.mark.asyncio
    async def test_live_research_state_deserialized_to_dict_not_str(self, svc):
        """收尾（full-scan-2026-07 批5 Codex）：live_research_state 也必須進
        _deserialize_session jsonb_fields 白名單——與姊妹欄 lr_dialog_snapshot 對稱。

        缺口：update_session allowed_fields 有 live_research_state（寫入走 _dumps_safe
        序列化成 JSON 字串），但 _deserialize_session 的 jsonb_fields 只列 lr_dialog_snapshot、
        漏 live_research_state → SQLite GET 回 JSON 字串而非 dict（API 消費端拿到字串）。

        【禁容錯掩蓋】此處強斷言 dict、絕不寫 `if isinstance(str): json.loads` ——
        那會在白名單漏加時假綠。修法＝jsonb_fields 補 live_research_state（root fix）。
        """
        sid = (await svc.create_session(USER_ID, ORG_ID, title="LRS"))['id']
        state = {'current_stage': 4, 'context_map_json': '{"topic":"x"}', 'sections': [1, 2]}
        await svc.update_session(sid, USER_ID, ORG_ID, {'live_research_state': state})
        row = await svc.get_session(sid, USER_ID, ORG_ID)
        lrs = row['live_research_state']
        assert isinstance(lrs, dict), \
            f"live_research_state 必回 dict 非字串（jsonb_fields 白名單漏加?）got {type(lrs)}"
        assert lrs == state
