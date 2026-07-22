"""Bad-file guard core — Plan B v8.1 G11 壞檔防護閉環的**可測邏輯核心**。

G11（`docs/in progress/plans/indexing-orchestrator-vm-plan.md` §G11）是 bash 控制流
（launcher.sh / orchestrator.sh + VPS 端 ledger.sh），unit test 難純測。本模組把
**兩塊機械可判的判定邏輯**抽成純函式，讓六個驗收場景在 pure Python 驗到（不碰 VPS/flock/GPU）：

  1. 輪末結算 settlement（launcher.sh 的 settle 呼叫 + ledger.sh 的 settle 動作同款判定）：
       candidates = failures 中 count≥閾值 且 ∉done 且 ∉skipped 且 **最後一次失敗在本 run**
       → verdict NOOP / PROMOTED / MASSCAP，上限 = min(10, ceil(本輪 gap 數 × 25%))。
  2. 輪級熔斷 circuit breaker（launcher.sh main 迴圈輪末）：
       每輪（無論 run_one 成敗）「done-set 未推進 且 本輪零 promote」→ streak+1；否則歸零；
       streak==2 → abort。

bash 端（ledger.sh / launcher.sh）與本模組**同一套判定**——bash 是 I/O 載體（flock/tmp→mv/
vps_ssh），判定規則在此定義並被測。bash 改動時對照本模組保持一致（真機驗證見 Phase D）。

**紀律**：純邏輯、零 I/O、零網路——絕不 SKIP，PASSED 計數即真綠（教訓 #2）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


# G11 第 1 件：skip 閾值 = 2；第 3 件：熔斷 streak == 2。
SKIP_THRESHOLD = 2
CIRCUIT_STREAK_LIMIT = 2

# 第 5 件 mass-skip 上限硬上界（取嚴式 min(10, ...)）。
MASS_CAP_ABS = 10
MASS_CAP_RATIO = 0.25


@dataclass(frozen=True)
class FailureRow:
    """failures.tsv 一行（一 tsv 一行）：<tsv>\\t<count>\\t<launcher_run_id>\\t<ts>。"""

    tsv: str
    count: int
    launcher_run_id: str
    ts: int = 0


class Verdict(str, Enum):
    NOOP = "NOOP"        # n == 0
    PROMOTED = "PROMOTED"  # 1 ≤ n ≤ cap
    MASSCAP = "MASSCAP"    # n > cap → skipped 零寫入 + quarantine + abort


@dataclass
class SettlementResult:
    """輪末結算結果。launcher 據此更新 skipped（PROMOTED）或 abort（MASSCAP）。"""

    verdict: Verdict
    candidates: list[str]     # 命中四條件的檔（PROMOTED=已寫 skipped / MASSCAP=落 quarantine）
    cap: int
    # promoted：本結算實際寫入 skipped 的檔（PROMOTED 時 == candidates；否則空——「零寫入」）
    promoted: list[str] = field(default_factory=list)

    @property
    def promote_count(self) -> int:
        """本輪 promote 數（熔斷 streak 的『本輪零新增 skip』判定用同一數，同快照同臨界區）。"""
        return len(self.promoted)


def mass_cap(round_gap_count: int) -> int:
    """mass-skip 上限 = min(10, ceil(本輪 gap 數 × 25%))（G11 第 5 件取嚴式）。

    防小週期（gap=12）時上限 10 形同全清單放行——ceil(12×0.25)=3 才是該週合理上界。
    round_gap_count==0 邊界：上限 0（本輪無 gap 不該有任何 promote）。
    """
    ratio_cap = math.ceil(round_gap_count * MASS_CAP_RATIO)
    return min(MASS_CAP_ABS, ratio_cap)


def compute_candidates(
    failures: list[FailureRow],
    done_set: set[str],
    skipped_set: set[str],
    launcher_run_id: str,
    threshold: int = SKIP_THRESHOLD,
) -> list[str]:
    """candidates 四條件（G11 第 5 件 + R8.1 第四條件）。

    count≥threshold 且 ∉done 且 ∉skipped 且 **最後一次失敗發生在本 launcher run**
    （FailureRow.launcher_run_id == 傳入 run_id；機械可判——mass-abort 保留的舊 run
    count=2 殘留變 inert，恢復 run 不誤掃、不誤 abort/promote）。

    回傳排序後檔名清單（穩定、可測）。
    """
    out: list[str] = []
    for row in failures:
        if row.count < threshold:
            continue
        if row.tsv in done_set:
            continue
        if row.tsv in skipped_set:
            continue
        if row.launcher_run_id != launcher_run_id:
            continue  # 第四條件：舊 run 殘留 inert
        out.append(row.tsv)
    return sorted(out)


def settle_round(
    failures: list[FailureRow],
    done_set: set[str],
    skipped_set: set[str],
    launcher_run_id: str,
    round_gap_count: int,
    threshold: int = SKIP_THRESHOLD,
) -> SettlementResult:
    """輪末單點結算（launcher 在 VPS flock 臨界區呼叫；本函式是其判定核心）。

    - n == 0            → NOOP（skipped 不動）。
    - 1 ≤ n ≤ cap       → PROMOTED（candidates 全寫 skipped + WARNING）。
    - n > cap           → MASSCAP（**skipped 零寫入**——「不生效」自然達成免回滾；
                          candidates 落 quarantine + CRITICAL abort；failures 一律保留）。

    cap = mass_cap(round_gap_count)。
    """
    cap = mass_cap(round_gap_count)
    cands = compute_candidates(failures, done_set, skipped_set, launcher_run_id, threshold)
    n = len(cands)
    if n == 0:
        return SettlementResult(verdict=Verdict.NOOP, candidates=[], cap=cap, promoted=[])
    if n <= cap:
        return SettlementResult(
            verdict=Verdict.PROMOTED, candidates=cands, cap=cap, promoted=list(cands)
        )
    # n > cap：零寫入（promoted 空）
    return SettlementResult(verdict=Verdict.MASSCAP, candidates=cands, cap=cap, promoted=[])


# ═══════════════════════════════════════════════════════════════════════════
# 輪級熔斷（launcher main 迴圈輪末，與成功路徑脫鉤）
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class CircuitState:
    """跨輪熔斷狀態（launcher 迴圈持有）。streak==limit → abort。"""

    streak: int = 0
    limit: int = CIRCUIT_STREAK_LIMIT

    @property
    def tripped(self) -> bool:
        return self.streak >= self.limit


def circuit_step(
    state: CircuitState,
    done_advanced: bool,
    promote_count: int,
) -> CircuitState:
    """一輪結束（**無論 run_one 成敗**）更新熔斷 streak（G11 第 3 件）。

    規則（輪級、與成功路徑脫鉤）：
      「done-set 未推進 且 本輪零 promote（零新增 skip）」→ streak+1；
      有推進 或 有 promote → streak 歸零。

    done_advanced：本輪 remaining 是否推進（done-set 有新增）。
    promote_count：本輪輪末結算實際 promote 進 skipped 的數（settle 的 promote_count，同快照）。

    回傳更新後 state（tripped==True 時 launcher abort + CRITICAL）。這一步覆蓋所有失敗模式
    （create fail / SSH never-ready / fetch fail-closed / bulk 全 fail）——它們都走「零推進零 promote」。
    """
    if (not done_advanced) and promote_count == 0:
        state.streak += 1
    else:
        state.streak = 0
    return state
