"""
verify_cov_lr.py — F3 CoV-lite LR 真實命中率 harness（Track F Task F4 補債）。

Purpose:
    量測 F3 CoV（run_cov_for_lr_section）在 LR per-section 用途下的真實命中率：
    - 組 A（應抓題）：CoV 偵測到捏造數字/引錯 evidence/精度灌水/無根因果
    - 組 B（should-PASS 探針）：CoV 不誤判忠實內容/同義改寫為 fabrication
    - 組 D（真實量級）：在接近 production 的 ≤12000 字 context 下 CoV 能正確判決

同時記錄：
- 每次 run_cov_for_lr_section 的 token 成本估算（call count × tier）
- 延遲（秒/fixture）
- verified / unverified / contradicted 三維分布

Usage:
    cd code/python && ../../myenv311/Scripts/python.exe tools/verify_cov_lr.py

!! 執行前須 CEO 點頭（燒真 LLM 錢）!!
!! 成本上限：最多 9 fixture × 2 call/fixture = 18 high-tier calls（~$0.2–0.5）。
   注意：claims 為空時 _verify short-circuit → 該 fixture 只 1 call；degraded
   路徑也只 1 call。實際 call 數 9~18，harness 依結果推估並輸出。!!

範圍與簡化假設（adversarial review round 1）：
    - 本 harness 為 **F3 CoV-only 隔離量測**：只測 run_cov_for_lr_section（F3
      wrapper/prompt path），不跑 _run_publish_gate 外圍 gate（F1 critic、feature
      flag、F1 skip、state storage、WARN marker）。
    - 「production-identical」僅指 F3 wrapper level（CriticAgent(handler) +
      run_cov_for_lr_section，與 orchestrator 同初始化、同 method）。
    - Production F3 escalation 需 f1_verdict != REJECT 才跑、WARN 需 f1_verdict==PASS；
      本 harness 假設 F1 initial PASS，量 CoV detection 層而非 production 最終 verdict。

Design notes:
    - CriticAgent 以 F3-wrapper production-identical 方式初始化（同 verify_l3_critic.py 的 _make_real_critic）
    - 無 monkeypatch / Mock / mock_bab / hardcode verdict
    - harness 不在 unit test suite 內（不進 pytest discover），顯式執行才跑
    - 組 C CEO 對抗題：**唯一 append 入口在下方 CASES.extend 區塊**（同 verify_l3_critic.py 慣例）

Escalation 閾值（來自 orchestrator._run_publish_gate Step 4，親驗 orchestrator.py:3996+）：
    - contradicted > 0 → auto-escalate REJECT（harness 驗 CoV 偵測層，不含 orchestrator gate）
    - unverified >= 3 → escalate WARN（production 另需 f1_verdict==PASS，harness 假設之）

Verdict 對應（CoV 層，非 orchestrator 層）：
    CoV 輸出不直接回傳 PASS/WARN/REJECT，而是 verified/unverified/contradicted 計數
    （+ 失敗時 degraded dict 帶 verification_status="unverified"）。
    harness 用下列規則換算 action：
        verification_status=="unverified"/有 verification_message → "error"（DEGRADED，記 MISS，不算 PASS）
        contradicted > 0 → "reject"
        unverified >= 3 → "warn"
        否則            → "pass"
"""

import asyncio
import sys
import os
import time

# ---------------------------------------------------------------------------
# Path setup — must run from code/python CWD
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_HERE)  # code/python
if _PYTHON_ROOT not in sys.path:
    sys.path.insert(0, _PYTHON_ROOT)

# Load .env from repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(_PYTHON_ROOT))
_ENV_FILE = os.path.join(_REPO_ROOT, ".env")
if os.path.exists(_ENV_FILE):
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)

# ---------------------------------------------------------------------------
# Import fixtures
# ---------------------------------------------------------------------------
from tests.unit.reasoning.test_cov_lr_fixtures import ALL_COV_FIXTURES  # noqa: E402

# ---------------------------------------------------------------------------
# CASES: 基礎 9 筆（4A+2B+3D）+ CEO 對抗題 append 區
# ---------------------------------------------------------------------------
CASES = list(ALL_COV_FIXTURES)

# === CEO 對抗題 append 處 ===
# 新增對抗題請 append 在此 CASES.extend([...]) 塊。
# !! 不要 append 在 if __name__ == "__main__" 之後 ——
#    asyncio.run() 已執行完畢，任何後置 append 不會被跑到。!!
# CASES.extend([
#     ("COV-CEO-1", {
#         "section_content": "...",
#         "evidence_text": "...",
#         "expected_cov_action": "reject",  # 或 "warn" / "pass"
#         "fabrication_type": "...",
#         "explanation": "...",
#     }),
# ])

# ---------------------------------------------------------------------------
# Production-identical CriticAgent 初始化（同 verify_l3_critic.py）
# ---------------------------------------------------------------------------
def _make_real_critic():
    """
    Instantiate CriticAgent 同 LiveResearchOrchestrator.critic_agent：
        CriticAgent(handler)  handler 只需 query_params。
    No mock, no monkeypatch — same code path as production.
    """
    from reasoning.agents.critic import CriticAgent

    class _MinimalHandler:
        query_params: dict = {}

    handler = _MinimalHandler()
    return CriticAgent(handler)


# ---------------------------------------------------------------------------
# Action 換算（模擬 orchestrator._run_publish_gate 的 escalation 閾值）
# ---------------------------------------------------------------------------
def _cov_result_to_action(cov_result: dict) -> str:
    """
    將 run_cov_for_lr_section 輸出換算為 pass/warn/reject/error。

    此換算隔離 CoV 層，不含 F1 gate 前置條件（假設 F1 initial PASS；見檔頭範圍說明）。

    !! degraded 防護（adversarial review B-1，親驗 critic.py:449-461）!!
    run_cov_verification 例外路徑回 **truthy** degraded dict：counts 全 0 但帶
    verification_status="unverified" + verification_message。若只看 counts 會回 "pass"，
    把「CoV 壞掉」誤讀成「CoV 判 PASS」= 假綠燈。故先攔 degraded → "error"。

    規則（orchestrator.py:3996+ Step 4，行號快照功能等價）：
        None / degraded(verification_status=="unverified" 或有 verification_message) → "error"
        contradicted > 0 → "reject"
        unverified >= 3 → "warn"
        否則            → "pass"
    """
    if cov_result is None:
        return "error"
    # degraded result 防護：例外路徑回 truthy dict 但帶 verification_status / message
    if (cov_result.get("verification_status") == "unverified"
            or cov_result.get("verification_message")):
        return "error"
    contradicted = cov_result.get("contradicted_count", 0)
    unverified = cov_result.get("unverified_count", 0)
    if contradicted > 0:
        return "reject"
    if unverified >= 3:
        return "warn"
    return "pass"


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------
async def run_verification():
    from core.config import CONFIG

    provider = CONFIG.llm_endpoints.get("openai")
    if provider and provider.models:
        model_high = provider.models.high
    else:
        model_high = "UNKNOWN"

    print("=" * 72)
    print("F3 CoV-LITE LR — REAL LLM DETECTION HARNESS (Track F Task F4)")
    print("=" * 72)
    print(f"  LLM model (high tier):     {model_high}")
    print(f"  Fixture count:             {len(CASES)}")
    print(f"  Calls per fixture:         1–2 (extract always; verify only if claims non-empty)")
    print(f"  Max calls (upper bound):   {len(CASES) * 2} high-tier; actual recorded below")
    print(f"  Estimated cost/run:        ~$0.2–0.5 上限 (rough, depends on context length)")
    print(f"  Scope:                     F3 CoV-only (no _run_publish_gate gate; assume F1 PASS)")
    print(f"  Mock/monkeypatch:          NONE")
    print(f"  Escalation thresholds:")
    print(f"    degraded/None       → error (記 MISS，不算 PASS)")
    print(f"    contradicted > 0    → reject")
    print(f"    unverified  >= 3    → warn")
    print(f"    otherwise           → pass")
    print("=" * 72)
    print()

    critic = _make_real_critic()
    results = []
    total_call_count = 0

    for fid, fx in CASES:
        section_content = fx["section_content"]
        evidence_text = fx["evidence_text"]
        expected_action = fx["expected_cov_action"]
        fab_type = fx["fabrication_type"]
        explanation = fx["explanation"]

        print(f"--- {fid} [{fab_type}] expected={expected_action.upper()} ---")
        print(f"    section: {section_content[:80]}...")
        print(f"    evidence: {evidence_text[:80]}...")
        print(f"    evidence_len: {len(evidence_text)} chars")

        t0 = time.monotonic()
        try:
            cov_result = await critic.run_cov_for_lr_section(
                section_content=section_content,
                chapter_evidence_text=evidence_text,
            )
            elapsed = time.monotonic() - t0

            actual_action = _cov_result_to_action(cov_result)
            match = (actual_action == expected_action)
            match_label = "MATCH" if match else "MISS"

            if cov_result:
                verified = cov_result.get("verified_count", 0)
                unverified = cov_result.get("unverified_count", 0)
                contradicted = cov_result.get("contradicted_count", 0)
                summary = cov_result.get("summary", "")[:100]
                results_list = cov_result.get("results", [])
            else:
                verified = unverified = contradicted = 0
                summary = "(no result)"
                results_list = []

            # SF-4：實際 call 數推估（不寫死 +2）。
            #   degraded（error）→ extract 已發但 verify 走 except → 1 call
            #   results 為空 + 非 degraded → claims 抽不到，_verify short-circuit → 1 call
            #   results 非空 → extract + verify → 2 call
            if actual_action == "error":
                est_calls = 1
            elif not results_list:
                est_calls = 1
            else:
                est_calls = 2
            total_call_count += est_calls

            print(f"    verdict:  {actual_action.upper()} → {match_label}  (est_calls={est_calls})")
            print(
                f"    counts:   verified={verified} unverified={unverified} "
                f"contradicted={contradicted}"
            )
            print(f"    summary:  {summary}")
            print(f"    claims:   {len(results_list)} result(s)")
            print(f"    elapsed:  {elapsed:.1f}s")
            print()

            results.append({
                "fid": fid,
                "fab_type": fab_type,
                "expected": expected_action,
                "actual": actual_action,
                "match": match,
                "verified": verified,
                "unverified": unverified,
                "contradicted": contradicted,
                "elapsed": elapsed,
                "evidence_len": len(evidence_text),
                "error": None,
                # Step 3b：保留 per-claim 明細供 SF-A2verify 人工核對（哪句被判 CONTRADICTED）
                "results_list": results_list,
            })

        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"    ERROR: {type(exc).__name__}: {exc}")
            print(f"    elapsed: {elapsed:.1f}s")
            print()
            results.append({
                "fid": fid,
                "fab_type": fab_type,
                "expected": expected_action,
                "actual": "error",   # 小寫，與 degraded_cases 過濾一致（AR R2 Codex nit）
                "match": False,
                "verified": 0,
                "unverified": 0,
                "contradicted": 0,
                "elapsed": elapsed,
                "evidence_len": len(evidence_text),
                "error": str(exc),
                "results_list": [],
            })

    # --- Summary table ---
    print("=" * 72)
    print("SUMMARY TABLE")
    print("=" * 72)
    header = (
        f"{'ID':<12} {'FAB_TYPE':<14} {'EXPECTED':<10} {'ACTUAL':<10} "
        f"{'V/U/C':<12} {'RESULT':<8} {'SEC':<6} {'EVLEN'}"
    )
    print(header)
    print("-" * 75)
    match_count = 0
    miss_list = []
    total_elapsed = 0.0
    for r in results:
        result_label = "MATCH" if r["match"] else "MISS "
        if r["match"]:
            match_count += 1
        else:
            miss_list.append(r["fid"])
        total_elapsed += r["elapsed"]
        vuc = f"{r['verified']}/{r['unverified']}/{r['contradicted']}"
        print(
            f"{r['fid']:<12} {r['fab_type']:<14} {r['expected'].upper():<10} "
            f"{r['actual'].upper():<10} {vuc:<12} {result_label:<8} "
            f"{r['elapsed']:.1f}s  {r['evidence_len']}c"
        )

    print("-" * 75)
    print(f"\nSCORE: {match_count}/{len(CASES)} verdict 符合預期")
    print(f"TOTAL ELAPSED: {total_elapsed:.1f}s")
    print(f"TOTAL LLM CALLS (actual est): {total_call_count} (high tier; upper bound {len(CASES) * 2})")

    # DEGRADED summary（B-1：CoV 故障 ≠ PASS）
    degraded_cases = [r for r in results if r["actual"] == "error"]
    print(f"\nDEGRADED/ERROR (CoV 故障，不算 PASS): {len(degraded_cases)}")
    for r in degraded_cases:
        print(f"  {r['fid']} expected={r['expected']} → DEGRADED/ERROR ({r.get('error') or 'degraded dict'})")

    # False-positive summary
    fp_cases = [r for r in results if r["expected"] == "pass" and r["actual"] not in ("pass",)]
    fn_cases = [r for r in results if r["expected"] != "pass" and r["actual"] == "pass"]
    print(f"\nFALSE POSITIVES (should-PASS but got WARN/REJECT/ERROR): {len(fp_cases)}")
    for r in fp_cases:
        print(f"  {r['fid']} expected=pass actual={r['actual']} V/U/C={r['verified']}/{r['unverified']}/{r['contradicted']}")
    print(f"FALSE NEGATIVES (should-catch but got PASS): {len(fn_cases)}")
    for r in fn_cases:
        print(f"  {r['fid']} expected={r['expected']} actual=pass V/U/C={r['verified']}/{r['unverified']}/{r['contradicted']}")

    # WARN-threshold 判讀輔助（SF-2）：warn/reject-expected 題印 raw counts，
    # 讓人區分「閾值未達」vs「detection 失敗」，不被 MATCH/MISS 黑箱誤導。
    print(f"\nRAW COUNTS (warn/reject-expected，判 threshold vs detection):")
    for r in results:
        if r["expected"] in ("warn", "reject"):
            print(f"  {r['fid']} expected={r['expected']} actual={r['actual']} "
                  f"V/U/C={r['verified']}/{r['unverified']}/{r['contradicted']}")

    if miss_list:
        print(f"\nMISS fixtures: {', '.join(miss_list)}")
        print("Investigate: is the escalation threshold too lenient? "
              "Or is the fixture unrealistically easy?")
    else:
        print("\nAll fixtures matched — F3 CoV detection intact.")

    print(f"\n[COST NOTE] 上限 ~$0.2–0.5/輪（{len(CASES) * 2} calls 上界）；"
          f"actual est {total_call_count} calls。精確用量請查 OpenAI 帳單。")
    print("[CEO NOTE] 補 adversarial case 的唯一入口：本檔上方 CASES.extend 區塊（勿動 fixtures 檔）。")
    print("=" * 72)

    # -----------------------------------------------------------------------
    # Step 3b：per-claim dump（可觀測性，不動 detection 邏輯）。
    #   對 COV-A-2 + 任何 expected != actual 的 fixture，逐條印 results 的
    #   claim / status / explanation（含 subject_entity 若有）。供 SF-A2verify
    #   人工核對「被判 CONTRADICTED 的是台鹽句」。輸出 UTF-8 寫檔（不走 console 管線）。
    # -----------------------------------------------------------------------
    dump_lines = []

    def _dump(line=""):
        print(line)
        dump_lines.append(line)

    _dump()
    _dump("=" * 72)
    _dump("PER-CLAIM DUMP (COV-A-2 + 所有 MISS fixture；SF-A2verify 人工核對材料)")
    _dump("=" * 72)
    dump_targets = [
        r for r in results
        if r["fid"] == "COV-A-2" or not r["match"]
    ]
    if not dump_targets:
        _dump("(無 COV-A-2 / 無 MISS — 無 per-claim dump 目標)")
    for r in dump_targets:
        _dump()
        _dump(f"### {r['fid']} expected={r['expected'].upper()} actual={r['actual'].upper()} "
              f"V/U/C={r['verified']}/{r['unverified']}/{r['contradicted']}")
        claims = r.get("results_list") or []
        if not claims:
            _dump("    (no per-claim results — extract 抽不到 claim / degraded / error)")
        for idx, c in enumerate(claims):
            claim_txt = c.get("claim", "")
            status = c.get("status", "")
            explanation = c.get("explanation", "")
            _dump(f"    [{idx}] status={status}")
            _dump(f"        claim:       {claim_txt}")
            if "subject_entity" in c:
                _dump(f"        subject_entity: {c.get('subject_entity')}")
            _dump(f"        explanation: {explanation}")

    # UTF-8 寫檔（Python 端，不走 console 管線；AR R2 Codex blocker 修正）
    _SCRATCH_DIR = os.path.join(_REPO_ROOT, "docs", "scratch")
    os.makedirs(_SCRATCH_DIR, exist_ok=True)
    _RESULTS_PATH = os.path.join(_SCRATCH_DIR, "cov-lr-harness-run3-results.md")
    summary_block = [
        f"# CoV LR Harness Run — A-2 subject_entity 結構修法驗收",
        "",
        f"- SCORE: {match_count}/{len(CASES)} verdict 符合預期",
        f"- TOTAL LLM CALLS (actual est): {total_call_count}",
        f"- FALSE POSITIVES (should-PASS): {[r['fid'] for r in fp_cases]}",
        f"- FALSE NEGATIVES (should-catch but PASS): {[r['fid'] for r in fn_cases]}",
        f"- DEGRADED/ERROR: {[r['fid'] for r in degraded_cases]}",
        f"- MISS: {miss_list}",
        "",
        "## SUMMARY TABLE",
        "```",
        header,
    ]
    for r in results:
        result_label = "MATCH" if r["match"] else "MISS "
        vuc = f"{r['verified']}/{r['unverified']}/{r['contradicted']}"
        summary_block.append(
            f"{r['fid']:<12} {r['fab_type']:<14} {r['expected'].upper():<10} "
            f"{r['actual'].upper():<10} {vuc:<12} {result_label:<8} "
            f"{r['elapsed']:.1f}s  {r['evidence_len']}c"
        )
    summary_block.append("```")
    summary_block.append("")
    summary_block.append("## PER-CLAIM DUMP")
    summary_block.append("```")
    summary_block.extend(dump_lines)
    summary_block.append("```")
    with open(_RESULTS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_block) + "\n")
    print(f"\n[RESULTS WRITTEN] {_RESULTS_PATH} (UTF-8)")

    return match_count, len(CASES), miss_list, results


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    match_count, total, miss_list, _ = asyncio.run(run_verification())
    if miss_list:
        sys.exit(1)
