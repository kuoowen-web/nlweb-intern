"""G11 壞檔防護閉環 — 輪末結算 + 熔斷 streak 可測核心測試（Plan B v8.1）。

**紀律**：純邏輯測試（零 I/O / 零網路 / 零 VPS）——**絕不 SKIP**，PASSED 計數即真綠
（教訓 #2：skipif 全跳＝假綠）。用 `uv run pytest code/python/tests/test_badfile_guard.py -v`。

覆蓋 G11 驗收六場景（plan §G11 驗收）的**可 pure-Python 判定**部分：
  ① 單一壞檔 輪1記帳→輪2入skip→輪3完成（結算 verdict 時序）
  ② 混檔不誤熔斷（好檔推進 → streak 歸零）
  ③ fetch-fail 兩輪熔斷（零推進零 promote → streak 2）
  ④ mass-cap 零寫入 abort（n>cap → MASSCAP、promoted 空）
  ⑤ mass 恢復 run 第四條件不誤 abort（舊 run 殘留 inert）
  ⑥ triage 移出後重試（skipped 含該檔 → 不再成 candidate）

**明確標記**：純 bash I/O 行為（flock 併發 / tmp→mv inode / vps_ssh 傳輸 / scp fetch
實際計數 / systemd）只能 Phase D 真機驗——見本檔末 docstring `PHASE_D_ONLY`。
"""
from __future__ import annotations

from indexing.badfile_guard import (
    CIRCUIT_STREAK_LIMIT,
    SKIP_THRESHOLD,
    CircuitState,
    FailureRow,
    Verdict,
    circuit_step,
    compute_candidates,
    mass_cap,
    settle_round,
)

RUN = "20260722-030000"       # 本 launcher run id
OLD_RUN = "20260715-030000"   # 舊 run（mass-abort 殘留）


# ═══════════════════════════════════════════════════════════════════════════
# mass_cap 取嚴式（min(10, ceil(gap×25%))）
# ═══════════════════════════════════════════════════════════════════════════


class TestMassCap:
    def test_small_week_ratio_binds(self):
        # gap=12 → ceil(3.0)=3；取嚴式擋掉「上限 10 形同全清單」
        assert mass_cap(12) == 3

    def test_large_week_abs_cap_binds(self):
        # gap=100 → ceil(25)=25，但硬上界 10
        assert mass_cap(100) == 10

    def test_boundary_exact(self):
        assert mass_cap(40) == 10   # ceil(10.0)=10 == abs cap
        assert mass_cap(44) == 10   # ceil(11.0)=11 → clamp 10

    def test_zero_gap(self):
        assert mass_cap(0) == 0     # 無 gap → 上限 0，任何候選都超 → 交人工


# ═══════════════════════════════════════════════════════════════════════════
# compute_candidates 四條件
# ═══════════════════════════════════════════════════════════════════════════


class TestCandidateFourConditions:
    def test_threshold_is_two(self):
        # count=1 不夠；count=2 才進候選（閾值 2）
        assert SKIP_THRESHOLD == 2
        rows = [FailureRow("a.tsv", 1, RUN), FailureRow("b.tsv", 2, RUN)]
        assert compute_candidates(rows, set(), set(), RUN) == ["b.tsv"]

    def test_exclude_done(self):
        rows = [FailureRow("a.tsv", 3, RUN)]
        assert compute_candidates(rows, {"a.tsv"}, set(), RUN) == []

    def test_exclude_already_skipped(self):
        rows = [FailureRow("a.tsv", 3, RUN)]
        assert compute_candidates(rows, set(), {"a.tsv"}, RUN) == []

    def test_fourth_condition_old_run_inert(self):
        """R8.1 第四條件：最後一次失敗在**舊 run** → inert，不成候選。"""
        rows = [FailureRow("a.tsv", 2, OLD_RUN)]
        assert compute_candidates(rows, set(), set(), RUN) == []

    def test_fourth_condition_this_run_counts(self):
        rows = [FailureRow("a.tsv", 2, RUN)]
        assert compute_candidates(rows, set(), set(), RUN) == ["a.tsv"]


# ═══════════════════════════════════════════════════════════════════════════
# 場景 ①：單一壞檔 輪1記帳 → 輪2入skip → 輪3完成
# ═══════════════════════════════════════════════════════════════════════════


class TestScenario1_SingleBadFile:
    """單一壞檔獨佔 gap，全程單次 run 內閉環（skip 閾值 2 × 結算）。"""

    def test_round1_count1_no_promote(self):
        # 輪 1 fail：count=1，結算 candidates 空
        rows = [FailureRow("bad.tsv", 1, RUN)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=1)
        assert r.verdict == Verdict.NOOP
        assert r.promote_count == 0

    def test_round2_count2_promoted(self):
        # 輪 2 fail：count=2，結算 promote 入 skip + WARNING
        rows = [FailureRow("bad.tsv", 2, RUN)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=1)
        assert r.verdict == Verdict.PROMOTED
        assert r.promoted == ["bad.tsv"]
        assert r.promote_count == 1

    def test_round3_skipped_removes_from_candidates(self):
        # 輪 3：bad.tsv 已在 skipped，差集扣除 → 不再是 candidate（remaining 歸零由 launcher 側完成）
        rows = [FailureRow("bad.tsv", 2, RUN)]
        r = settle_round(rows, done_set=set(), skipped_set={"bad.tsv"},
                         launcher_run_id=RUN, round_gap_count=1)
        assert r.verdict == Verdict.NOOP


# ═══════════════════════════════════════════════════════════════════════════
# 場景 ②：混檔不誤熔斷
# ═══════════════════════════════════════════════════════════════════════════


class TestScenario2_MixedNoFalseTrip:
    """壞檔 + ≥1 好檔：好檔推進 done → 熔斷 streak 歸零（不誤觸）。"""

    def test_good_file_advances_resets_streak(self):
        st = CircuitState()
        # 前一輪曾 streak=1（假設先前一輪零推進）
        st = circuit_step(st, done_advanced=False, promote_count=0)
        assert st.streak == 1
        # 本輪好檔推進 done → 歸零
        st = circuit_step(st, done_advanced=True, promote_count=0)
        assert st.streak == 0
        assert not st.tripped

    def test_bad_file_promote_also_resets(self):
        """本輪雖 done 未推進，但壞檔入 skip（promote>0）→ 有進展 → streak 歸零。"""
        st = CircuitState(streak=1)
        st = circuit_step(st, done_advanced=False, promote_count=1)
        assert st.streak == 0
        assert not st.tripped


# ═══════════════════════════════════════════════════════════════════════════
# 場景 ③：fetch-fail / SSH never-ready 兩輪熔斷
# ═══════════════════════════════════════════════════════════════════════════


class TestScenario3_FetchFailTwoRoundTrip:
    """非檔案類失敗（fetch fail-closed / SSH never-ready）：無 per-file 記帳、
    零 skip 進帳 → 每輪零推進零 promote → streak 2 熔斷。上界 = 2 台 VM。"""

    def test_two_rounds_trip(self):
        st = CircuitState()
        st = circuit_step(st, done_advanced=False, promote_count=0)  # 輪 1
        assert st.streak == 1
        assert not st.tripped
        st = circuit_step(st, done_advanced=False, promote_count=0)  # 輪 2
        assert st.streak == 2
        assert st.tripped   # → launcher abort + CRITICAL

    def test_limit_is_two(self):
        assert CIRCUIT_STREAK_LIMIT == 2


# ═══════════════════════════════════════════════════════════════════════════
# 場景 ④：mass-cap 零寫入 abort
# ═══════════════════════════════════════════════════════════════════════════


class TestScenario4_MassCapZeroWrite:
    """模擬 tunnel/PG 全斷：一輪大量檔 count 達閾值 → n>cap → skipped 零寫入 + abort。"""

    def test_masscap_zero_write(self):
        # 本輪 gap=20 → cap=min(10, ceil(5))=5；12 個壞檔候選 > 5
        rows = [FailureRow(f"f{i}.tsv", 2, RUN) for i in range(12)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=20)
        assert r.verdict == Verdict.MASSCAP
        assert r.cap == 5
        assert len(r.candidates) == 12
        assert r.promoted == []        # 零寫入（免回滾）
        assert r.promote_count == 0    # 熔斷判定用同一數

    def test_exactly_at_cap_promotes(self):
        # 邊界：n == cap → PROMOTED（不 abort）
        rows = [FailureRow(f"f{i}.tsv", 2, RUN) for i in range(5)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=20)
        assert r.verdict == Verdict.PROMOTED
        assert len(r.promoted) == 5


# ═══════════════════════════════════════════════════════════════════════════
# 場景 ⑤：mass 事後恢復 run 第四條件不誤 abort（R8.1）
# ═══════════════════════════════════════════════════════════════════════════


class TestScenario5_MassRecoveryRun:
    """故障修復後重啟：舊 run 殘留 count=2（未清）不該讓恢復 run 誤 abort/誤 promote。"""

    def test_old_run_residue_inert(self):
        # 12 個舊 run 殘留（count=2, run=OLD_RUN），本 run 尚無新失敗
        rows = [FailureRow(f"f{i}.tsv", 2, OLD_RUN) for i in range(12)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=20)
        # 第四條件把舊 run 全濾掉 → NOOP，不 abort、不 promote
        assert r.verdict == Verdict.NOOP
        assert r.candidates == []

    def test_recovery_run_single_fail_becomes_candidate(self):
        """明文接受的跨事件 2-strike：殘留 count 檔在新 run 單次失敗（+1→3）即候選，收斂更快。"""
        # 恢復 run 對 f0 又失敗一次 → count 3、run 更新為本 RUN
        rows = [FailureRow("f0.tsv", 3, RUN)] + [FailureRow(f"f{i}.tsv", 2, OLD_RUN) for i in range(1, 12)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=20)
        # 只有 f0（本 run）成候選；舊 run 11 檔 inert → n=1 ≤ cap → PROMOTED（不誤 mass-abort）
        assert r.verdict == Verdict.PROMOTED
        assert r.promoted == ["f0.tsv"]


# ═══════════════════════════════════════════════════════════════════════════
# 場景 ⑥：人工 triage 移出後重試
# ═══════════════════════════════════════════════════════════════════════════


class TestScenario6_TriageReadd:
    """triage 修好：從 skipped 刪行 + failures 歸零 → 下輪該檔重新可做（不再 candidate）。"""

    def test_after_clear_not_candidate(self):
        # triage 後 failures 已歸零（該檔不在 failures 清單）→ compute_candidates 自然不含
        rows: list[FailureRow] = []  # 歸零＝刪行
        assert compute_candidates(rows, set(), set(), RUN) == []

    def test_still_in_skipped_before_clear_excluded(self):
        # 若只從 failures 歸零但 skipped 未刪 → 仍被差集扣除（safe：不會重撞）
        rows = [FailureRow("bad.tsv", 2, RUN)]
        r = settle_round(rows, done_set=set(), skipped_set={"bad.tsv"},
                         launcher_run_id=RUN, round_gap_count=1)
        assert r.verdict == Verdict.NOOP  # 已在 skipped → 不重複 promote


# ═══════════════════════════════════════════════════════════════════════════
# 熔斷與結算的耦合點：promote_count 同快照
# ═══════════════════════════════════════════════════════════════════════════


class TestCircuitSettlementCoupling:
    def test_promote_feeds_circuit_reset(self):
        """輪末結算 promote>0 → 同數餵熔斷 → streak 歸零（同臨界區同快照，天然一致）。"""
        rows = [FailureRow("bad.tsv", 2, RUN)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=1)
        st = CircuitState(streak=1)
        st = circuit_step(st, done_advanced=False, promote_count=r.promote_count)
        assert st.streak == 0  # 壞檔入 skip 算進展

    def test_masscap_promote_zero_does_not_reset(self):
        """mass-cap verdict 的 promote_count==0 → 若該輪也零推進，streak 照加（不因『有候選』誤歸零）。"""
        rows = [FailureRow(f"f{i}.tsv", 2, RUN) for i in range(12)]
        r = settle_round(rows, done_set=set(), skipped_set=set(),
                         launcher_run_id=RUN, round_gap_count=20)
        assert r.promote_count == 0
        st = CircuitState(streak=1)
        st = circuit_step(st, done_advanced=False, promote_count=r.promote_count)
        # 注意：mass-cap 本身即 abort（exit 4），此處僅驗 promote_count 語義不誤歸零
        assert st.streak == 2


# ─────────────────────────────────────────────────────────────────────────────
# PHASE_D_ONLY：以下只能真機（Phase D 故障注入）驗，pure-Python 測不到——
#   - ledger.sh flock 併發臨界區（兩 vps_ssh 同時 bump 同檔）的原子性
#   - tmp→mv 換 inode 後 flock 對固定 lockfile 仍有效（R9 nit）
#   - orchestrator per-file 失敗真的觸發單一 vps_ssh bump / 成功觸發 clear
#   - launcher TSV fetch「fetched==expected」assert 對真實 scp 部分失敗的攔截
#   - systemd oneshot/timer 語義、GPU VM lifecycle
#   本檔驗的是「給定 failures/done/skip 快照 → verdict/streak」的判定正確性（G11 心臟）。
# ─────────────────────────────────────────────────────────────────────────────
