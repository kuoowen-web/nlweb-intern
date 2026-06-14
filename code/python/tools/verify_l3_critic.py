"""
verify_l3_critic.py — L3 publish gate critic detection harness (Track F regression tool).

Purpose:
    Verify that the real LLM-backed F1 per-section critic can detect claim-level
    fabrication across all 7 fixture types. This is NOT a unit test (unit tests
    mock the LLM and prove nothing about detection). This harness fires real LLM
    calls against the production critic path.

Usage:
    cd code/python && ../../myenv311/Scripts/python.exe tools/verify_l3_critic.py

Design:
    - CASES list holds all fixture entries. Append adversarial cases at the bottom
      (see comment block at end of file).
    - CriticAgent is instantiated the same way the production LR orchestrator does it:
        CriticAgent(handler)  where handler carries query_params={}
    - No monkeypatch / Mock / mock_bab / hardcode verdict anywhere in this file.

Short-circuit note (F-CL-7):
    The guard_failed short-circuit lives in orchestrator._run_publish_gate (checks
    status != "drafted" before calling the agent). The agent method
    review_section_publish_gate itself has NO short-circuit — it will call the LLM
    for any section including guard_failed ones. F-CL-7 expected_critic_action="pass"
    refers to the orchestrator-level behavior; at the raw agent level we expect the
    LLM to likely PASS (or WARN) since the section content is a boilerplate guard
    message with no fabricated claims against evidence. We track this faithfully.
"""

import asyncio
import sys
import os

# ---------------------------------------------------------------------------
# Path setup — must run from code/python CWD
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_HERE)  # code/python
if _PYTHON_ROOT not in sys.path:
    sys.path.insert(0, _PYTHON_ROOT)

# Load .env from repo root (two levels up from code/python)
_REPO_ROOT = os.path.dirname(os.path.dirname(_PYTHON_ROOT))
_ENV_FILE = os.path.join(_REPO_ROOT, ".env")
if os.path.exists(_ENV_FILE):
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)

# ---------------------------------------------------------------------------
# Import fixtures
# ---------------------------------------------------------------------------
from tests.unit.reasoning.test_claim_fabrication_fixtures import ALL_FIXTURES  # noqa: E402

# ---------------------------------------------------------------------------
# CASES: list of (fixture_id, fixture_dict) — the 7 canonical fabrication cases
# ---------------------------------------------------------------------------
CASES = list(ALL_FIXTURES)  # [("F-CL-1", {...}), ..., ("F-CL-7", {...})]

# ---------------------------------------------------------------------------
# Adversarial cases — generalization + over-rejection probes (F-ADV-1..6)
# Insert here (after CASES definition, before if __name__ == "__main__") so
# they are actually executed. DO NOT append after asyncio.run() — that code
# path is never reached.
# ---------------------------------------------------------------------------
from tests.unit.reasoning.test_claim_fabrication_fixtures import _section  # noqa: E402
CASES.extend([
    ("F-ADV-1", {
        "section": _section("千葉縣的離岸風電在 2021 年達成裝置容量 3.2 GW [1]，為亞洲領先案例。", sources=[1], title="國外案例"),
        "evidence_text": "[1] 台灣離岸風電發展 — 台灣自 2019 年起推動離岸風電，彰化外海為主要場址，目標 2025 年達成階段性裝置容量。",
        "expected_fabrication_type": "other",
        "expected_critic_action": "reject",
        "explanation": "千葉縣完全不在 evidence（evidence 只講台灣）— 編造地名 + 數字，demo-killer 直球。",
    }),
    ("F-ADV-2", {
        "section": _section("丹麥在 2020 年離岸風電裝置容量達 7.7 GW [1]，居歐洲之冠。", sources=[1], title="國外案例"),
        "evidence_text": "[1] 歐洲離岸風電 2020 — 丹麥 2020 年離岸風電裝置容量約 1.7 GW；德國同年達 7.7 GW，為歐洲最高。",
        "expected_fabrication_type": "numeric",
        "expected_critic_action": "reject",
        "explanation": "張冠李戴最難類：7.7 GW 與「歐洲之冠」其實是德國的，section 安到丹麥。數字真在 evidence 但配錯主體。",
    }),
    ("F-ADV-3", {
        "section": _section("德國再生能源法 (EEG) 於 2000 年制定 [1]。", sources=[1], title="政策背景"),
        "evidence_text": "[1] 德國再生能源法 — 德國再生能源法 (EEG) 自 2000 年制定以來經多次修訂。",
        "expected_fabrication_type": "other",
        "expected_critic_action": "pass",
        "explanation": "should-PASS 探針：完全忠實 evidence。critic 若 REJECT/WARN = 過度拒絕（false positive）。",
    }),
    ("F-ADV-4", {
        "section": _section("德國再生能源占比近年明顯成長 [1]。", sources=[1], title="趨勢"),
        "evidence_text": "[1] 德國能源轉型 — 德國再生能源占比近年顯著提升。",
        "expected_fabrication_type": "other",
        "expected_critic_action": "pass",
        "explanation": "should-PASS 探針：「明顯成長」≈ evidence「顯著提升」同義改寫，不該當 fabrication。",
    }),
    ("F-ADV-5", {
        "section": _section("再生能源占比已達 32.4% [1]。", sources=[1], title="數據"),
        "evidence_text": "[1] 能源占比 — 再生能源占比約三成。",
        "expected_fabrication_type": "numeric",
        "expected_critic_action": "reject",
        "explanation": "精度灌水：evidence「約三成」→ section 捏造精確「32.4%」。WARN 也算抓到（非 PASS 即可），REJECT 更佳。",
    }),
    ("F-ADV-6", {
        "section": _section("碳定價是德國再生能源占比成長的主因 [1]。", sources=[1], title="政策影響"),
        "evidence_text": "[1] 德國能源轉型 — 德國再生能源占比成長受多重因素影響，包含碳定價、技術成本下降、社會接受度提升、EEG 法案等。",
        "expected_fabrication_type": "causal",
        "expected_critic_action": "warn",
        "explanation": "因果單一歸因：evidence 列多重因素，section 強化成單一「主因」，evidence 不支持。",
    }),
])

# ---------------------------------------------------------------------------
# Production-identical critic instantiation
# (mirrors reasoning/live_research/orchestrator.py critic_agent property)
# ---------------------------------------------------------------------------
def _make_real_critic():
    """
    Instantiate CriticAgent the same way LiveResearchOrchestrator.critic_agent does:

        from reasoning.agents.critic import CriticAgent
        self._critic_agent = CriticAgent(self.handler)

    handler only needs query_params (used by ask_llm fallback path).
    No mock, no monkeypatch — same code path as production.
    """
    from reasoning.agents.critic import CriticAgent

    class _MinimalHandler:
        """Minimal handler stub: only query_params required by ask_llm / TypeAgent paths."""
        query_params: dict = {}

    handler = _MinimalHandler()
    critic = CriticAgent(handler)
    return critic


# ---------------------------------------------------------------------------
# Verdict normalisation: map LLM verdict → expected_critic_action space
# ---------------------------------------------------------------------------
_VERDICT_TO_ACTION = {
    "PASS": "pass",
    "WARN": "warn",
    "REJECT": "reject",
}


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------
async def run_verification():
    from core.config import CONFIG

    # --- Print real model name (proof this is not a mock) ---
    provider = CONFIG.llm_endpoints.get("openai")
    if provider and provider.models:
        model_high = provider.models.high
        typeagent_cfg = CONFIG.reasoning_params.get("typeagent", {})
        typeagent_enabled = typeagent_cfg.get("enabled", False)
    else:
        model_high = "UNKNOWN"
        typeagent_enabled = False

    print("=" * 72)
    print("L3 PUBLISH GATE CRITIC — REAL LLM DETECTION HARNESS")
    print("=" * 72)
    print(f"  LLM model (high tier): {model_high}")
    print(f"  TypeAgent enabled:     {typeagent_enabled}")
    print(f"  Fixture count:         {len(CASES)}")
    print(f"  Mock/monkeypatch:      NONE")
    print("=" * 72)
    print()

    critic = _make_real_critic()

    results = []

    for fid, fx in CASES:
        section = fx["section"]
        evidence_text = fx["evidence_text"]
        expected_action = fx["expected_critic_action"]   # "reject"/"warn"/"pass"
        fab_type = fx["expected_fabrication_type"]
        explanation = fx["explanation"]

        print(f"--- {fid} [{fab_type}] expected={expected_action.upper()} ---")
        print(f"    section: {section.section_content[:80]}...")
        print(f"    evidence: {evidence_text[:80]}...")

        try:
            review = await critic.review_section_publish_gate(
                section=section,
                section_index=0,
                chapter_evidence_text=evidence_text,
                # warned_critic_claims=None, time_constraint=None (default — not testing those)
            )

            actual_verdict = review.verdict          # "PASS" / "WARN" / "REJECT"
            actual_action = _VERDICT_TO_ACTION.get(actual_verdict, actual_verdict.lower())
            match = (actual_action == expected_action)
            match_label = "MATCH" if match else "MISS"

            # Print claim-level detail
            if review.claim_issues:
                claim_summary = "; ".join(
                    f"[{ci.claim_type}/{ci.severity}] {ci.claim_text[:60]}"
                    for ci in review.claim_issues
                )
            else:
                claim_summary = "(no claim_issues)"

            print(f"    verdict:  {actual_verdict} → {match_label}")
            print(f"    overall:  {review.overall_explanation[:120]}")
            print(f"    claims:   {claim_summary}")
            print()

            results.append({
                "fid": fid,
                "fab_type": fab_type,
                "expected": expected_action,
                "actual": actual_action,
                "verdict": actual_verdict,
                "match": match,
                "claim_issues": review.claim_issues,
                "overall_explanation": review.overall_explanation,
                "error": None,
            })

        except Exception as exc:
            print(f"    ERROR: {type(exc).__name__}: {exc}")
            print()
            results.append({
                "fid": fid,
                "fab_type": fab_type,
                "expected": expected_action,
                "actual": "ERROR",
                "verdict": "ERROR",
                "match": False,
                "claim_issues": [],
                "overall_explanation": f"Exception: {exc}",
                "error": exc,
            })

    # --- Summary table ---
    print("=" * 72)
    print("SUMMARY TABLE")
    print("=" * 72)
    header = f"{'ID':<8} {'FAB_TYPE':<14} {'EXPECTED':<10} {'ACTUAL':<10} {'RESULT':<8}"
    print(header)
    print("-" * 55)
    match_count = 0
    miss_list = []
    for r in results:
        result_label = "MATCH" if r["match"] else "MISS "
        if r["match"]:
            match_count += 1
        else:
            miss_list.append(r["fid"])
        print(
            f"{r['fid']:<8} {r['fab_type']:<14} {r['expected'].upper():<10} "
            f"{r['actual'].upper():<10} {result_label}"
        )

    print("-" * 55)
    print(f"\nSCORE: {match_count}/{len(CASES)} verdict 符合預期")

    if miss_list:
        print(f"\nMISS fixtures (critic 未偵測到 fabrication): {', '.join(miss_list)}")
        print("These are real detection gaps — investigate critic prompt coverage.")
    else:
        print("\nAll fixtures matched — L3 critic detection is intact.")

    print("=" * 72)
    return match_count, len(CASES), miss_list


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    match_count, total, miss_list = asyncio.run(run_verification())
    # Exit non-zero if any miss so CI can catch regressions
    if miss_list:
        sys.exit(1)


# === CEO 對抗題 append 處（同 fixture dict 格式）===
# 新增 adversarial case 請插入 line 57 "CASES = list(ALL_FIXTURES)" 下方的
# CASES.extend([...]) 區塊。
# !! 不要 append 在此處（if __name__ == "__main__" 之後）—— asyncio.run()
#    已經執行完畢，任何後置 append 都不會被跑到。!!
