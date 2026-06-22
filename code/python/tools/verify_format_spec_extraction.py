"""
verify_format_spec_extraction.py — H3 InitialFormatSpec 抽取準確度真 LLM harness。

Purpose:
    量測 AssociatorAgent.extract_initial_format_spec（Stage 1 進場一次 LLM call）的
    真實抽取品質：
    - 組 A（FSE-1, FSE-3）：明確 spec → 應抽中，斷言欄位值吻合 user 原文
    - 組 B（FSE-2）：章數-only → chapters 應留空（不自編標題）
    - 組 D（FSE-4）：純主題 → 全欄位 None/空（防過度抽取，should-PASS 探針）
    - 組 E（FSE-5）：模糊語句 → 保守 default，不腦補具體數字

    FSE-4 是 false-positive guard（與 FSE-1 成對照組）：
      FSE-1 測「有明說 → 抽得到」；FSE-4 測「沒明說 → 不腦補」。

Usage:
    cd code/python && ..\\..\\myenv311\\Scripts\\python.exe tools/verify_format_spec_extraction.py

!! 執行前須 CEO 點頭（燒真 LLM 錢）!!
!! 成本上限：low-tier call，1 call/fixture，5 fixtures = 5 low-tier calls/輪。
   low-tier 遠比 high-tier 便宜。粗估 ~$0.02–0.05/輪。
   精確用量請查 OpenAI 帳單。!!

Design notes:
    - AssociatorAgent 以 production-identical 方式初始化（同 associator.py）
    - 無 monkeypatch / Mock / mock 路徑
    - harness 不在 unit test suite 內（不進 pytest discover），顯式執行才跑
    - 組 CEO 對抗題：唯一 append 入口在下方 CASES.extend 區塊
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
# CASES: 基礎 5 筆（A×2 + B×1 + D×1 + E×1）+ CEO 對抗題 append 區
# ---------------------------------------------------------------------------
CASES = [
    (
        "FSE-1",
        {
            "query": (
                "請幫我研究台灣再生能源政策，"
                "分成：前言、國際趨勢、國內現況、案例分析、結論五章，"
                "總共約 7000 字，案例分析章放一張五國比較表格，用 APA 引用"
            ),
            "group": "A",
            "description": "明確 spec — 5 章標題 + 總字數 + 表格 + APA 引用",
            "expected_chapters_count": 5,
            "expected_chapter_names": ["前言", "國際趨勢", "國內現況", "案例分析", "結論"],
            "expected_total_word_count": 7000,
            "expected_has_table": True,
            "expected_table_target_chapter": "案例分析",
            "expected_citation_style": "author_year",
            "should_extract": True,
        },
    ),
    (
        "FSE-2",
        {
            "query": "幫我研究台灣半導體產業，分成五章",
            "group": "B",
            "description": "章數-only 契約 B — 只說「五章」無標題，chapters 應留空",
            "expected_chapters_count": 0,  # 不自編標題 → chapters 空 list
            "expected_chapter_names": [],
            "expected_total_word_count": None,
            "expected_has_table": False,
            "expected_table_target_chapter": None,
            "expected_citation_style": None,
            "should_extract": False,  # chapters 不應自編
        },
    ),
    (
        "FSE-3",
        {
            "query": (
                "請研究台灣人工智慧發展，"
                "分成：緒論、技術現況、產業應用、挑戰與展望四章，"
                "第一章 2000 字、第二章 3000 字"
            ),
            "group": "A",
            "description": "逐章字數 — 章標題 + per-chapter word_target",
            "expected_chapters_count": 4,
            "expected_chapter_names": ["緒論", "技術現況", "產業應用", "挑戰與展望"],
            "expected_chapter_word_targets": {
                "緒論": 2000,
                "技術現況": 3000,
                "產業應用": None,
                "挑戰與展望": None,
            },
            "expected_total_word_count": None,
            "expected_has_table": False,
            "expected_citation_style": None,
            "should_extract": True,
        },
    ),
    (
        "FSE-4",
        {
            "query": "台灣綠能發展現況",
            "group": "D",
            "description": (
                "純主題 should-PASS 探針 — 無任何格式指定，"
                "全欄位應 None/空（false-positive guard，與 FSE-1 成對照組）"
            ),
            "expected_chapters_count": 0,
            "expected_chapter_names": [],
            "expected_total_word_count": None,
            "expected_has_table": False,
            "expected_citation_style": None,
            "should_extract": False,  # false-positive guard
        },
    ),
    (
        "FSE-5",
        {
            "query": "幫我研究台灣食安問題，寫詳細一點，章節多分幾章",
            "group": "E",
            "description": (
                "模糊不腦補探針 — 無具體數字/標題，"
                "不應腦補出具體章數/字數（保守 default）"
            ),
            "expected_chapters_count": 0,
            "expected_chapter_names": [],
            "expected_total_word_count": None,
            "expected_has_table": False,
            "expected_citation_style": None,
            "should_extract": False,  # 保守 default，不腦補
        },
    ),
]

# === CEO 對抗題 append 處 ===
# 新增對抗題請 append 在此 CASES.extend([...]) 塊。
# !! 不要 append 在 if __name__ == "__main__" 之後 ——
#    asyncio.run() 已執行完畢，任何後置 append 不會被跑到。!!
#
# CEO NOTE — 設計案例自測必 overfit，對抗題須來自設計者以外。
# 格式參考：
# CASES.extend([
#     (
#         "FSE-CEO-1",
#         {
#             "query": "...",
#             "group": "CEO",
#             "description": "...",
#             "expected_chapters_count": N,
#             "expected_chapter_names": [...],
#             "expected_total_word_count": N_or_None,
#             "expected_has_table": True_or_False,
#             "expected_citation_style": "author_year" | "numeric" | "footnote" | "none" | None,
#             "should_extract": True_or_False,
#         },
#     ),
# ])


# ---------------------------------------------------------------------------
# Production-identical AssociatorAgent 初始化
# ---------------------------------------------------------------------------
def _make_real_associator():
    """
    Instantiate AssociatorAgent production-identical。
    No mock, no monkeypatch — same code path as production.
    """
    from reasoning.agents.associator import AssociatorAgent

    class _MinimalHandler:
        query_params: dict = {}

    handler = _MinimalHandler()
    return AssociatorAgent(handler)


# ---------------------------------------------------------------------------
# Per-fixture assertion logic
# ---------------------------------------------------------------------------
def _check_fixture(fid: str, fx: dict, spec) -> tuple[bool, list[str], list[str]]:
    """
    對 InitialFormatSpec 做逐欄斷言。

    Returns:
        (all_pass, fn_violations, fp_violations)
        fn_violations: FALSE NEGATIVE（應抽中 but 沒抽到/抽錯）
        fp_violations: FALSE POSITIVE（應留空 but 腦補了）
    """
    fn_violations = []
    fp_violations = []

    # --- chapters ---
    expected_count = fx["expected_chapters_count"]
    actual_chapters = spec.chapters or []
    actual_count = len(actual_chapters)

    if fx["should_extract"] and expected_count > 0:
        # 組 A：應抽中章節
        if actual_count != expected_count:
            fn_violations.append(
                f"chapters count: expected={expected_count}, actual={actual_count}"
            )
        else:
            # 逐章標題比對
            expected_names = fx.get("expected_chapter_names", [])
            for i, (exp_name, ch) in enumerate(zip(expected_names, actual_chapters)):
                if exp_name not in ch.name:
                    fn_violations.append(
                        f"chapters[{i}].name: expected contains '{exp_name}', actual='{ch.name}'"
                    )
            # 逐章字數（若 fixture 有 expected_chapter_word_targets）
            if "expected_chapter_word_targets" in fx:
                targets = fx["expected_chapter_word_targets"]
                for ch in actual_chapters:
                    if ch.name in targets:
                        exp_wt = targets[ch.name]
                        if exp_wt is not None and ch.word_target != exp_wt:
                            fn_violations.append(
                                f"chapters['{ch.name}'].word_target: expected={exp_wt}, actual={ch.word_target}"
                            )
    else:
        # 組 B/D/E：chapters 應空
        if actual_count > 0:
            actual_names = [ch.name for ch in actual_chapters]
            fp_violations.append(
                f"chapters should be empty, but got {actual_count} chapters: {actual_names}"
            )

    # --- total_word_count ---
    exp_wc = fx["expected_total_word_count"]
    actual_wc = spec.total_word_count
    if exp_wc is not None:
        if actual_wc != exp_wc:
            fn_violations.append(
                f"total_word_count: expected={exp_wc}, actual={actual_wc}"
            )
    else:
        if actual_wc is not None:
            fp_violations.append(
                f"total_word_count should be None, but got {actual_wc}"
            )

    # --- citation_style ---
    exp_cit = fx["expected_citation_style"]
    actual_cit = spec.citation_style
    if exp_cit is not None:
        if actual_cit != exp_cit:
            fn_violations.append(
                f"citation_style: expected='{exp_cit}', actual='{actual_cit}'"
            )
    else:
        if actual_cit is not None:
            fp_violations.append(
                f"citation_style should be None, but got '{actual_cit}'"
            )

    # --- special_elements (table) ---
    actual_elements = spec.special_elements or []
    actual_has_table = any(e.type == "table" for e in actual_elements)
    exp_has_table = fx["expected_has_table"]

    if exp_has_table:
        if not actual_has_table:
            fn_violations.append("special_elements: expected table, but no table found")
        else:
            exp_table_chapter = fx.get("expected_table_target_chapter")
            if exp_table_chapter:
                table_el = next((e for e in actual_elements if e.type == "table"), None)
                if table_el and exp_table_chapter not in table_el.target_chapter:
                    fn_violations.append(
                        f"table.target_chapter: expected contains '{exp_table_chapter}', "
                        f"actual='{table_el.target_chapter}'"
                    )
    else:
        if actual_has_table:
            fp_violations.append(
                f"special_elements: should have no table, but found one"
            )

    all_pass = not fn_violations and not fp_violations
    return all_pass, fn_violations, fp_violations


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------
async def run_verification():
    from core.config import CONFIG

    provider = CONFIG.llm_endpoints.get("openai")
    if provider and provider.models:
        model_low = provider.models.low
    else:
        model_low = "UNKNOWN"

    print("=" * 72)
    print("INITIAL FORMAT SPEC EXTRACTION — REAL LLM HARNESS (H3 branch)")
    print("=" * 72)
    print(f"  LLM model (low tier):      {model_low}")
    print(f"  Fixture count:             {len(CASES)}")
    print(f"  Calls per fixture:         1 (low-tier; extract only)")
    print(f"  Total calls (upper bound): {len(CASES)} low-tier calls")
    print(f"  Estimated cost/run:        ~$0.02–0.05 (low tier, short outputs)")
    print(f"  Scope:                     extract_initial_format_spec 隔離量測")
    print(f"                             (no full Stage 1; direct agent call)")
    print(f"  Mock/monkeypatch:          NONE")
    print(f"  此 harness 燒真 LLM 錢，跑前須 CEO 點頭")
    print("=" * 72)
    print()

    agent = _make_real_associator()
    results = []
    all_fn = []  # cumulative false negatives
    all_fp = []  # cumulative false positives

    for fid, fx in CASES:
        query = fx["query"]
        group = fx["group"]
        description = fx["description"]

        print(f"--- {fid} [group={group}] ---")
        print(f"    desc:    {description}")
        print(f"    query:   {query[:100]}{'...' if len(query) > 100 else ''}")

        t0 = time.monotonic()
        try:
            spec = await agent.extract_initial_format_spec(query)
            elapsed = time.monotonic() - t0

            # Per-fixture dump of actual extracted values
            actual_chapters = spec.chapters or []
            actual_elements = spec.special_elements or []

            print(f"    -- extracted spec dump --")
            print(f"    total_word_count: {spec.total_word_count}")
            print(f"    citation_style:   {spec.citation_style}")
            print(f"    chapters ({len(actual_chapters)}):")
            for i, ch in enumerate(actual_chapters):
                print(f"      [{i}] name='{ch.name}' word_target={ch.word_target}")
            print(f"    special_elements ({len(actual_elements)}):")
            for i, el in enumerate(actual_elements):
                print(f"      [{i}] type='{el.type}' target_chapter='{el.target_chapter}' desc='{el.description}'")

            # Run assertions
            all_pass, fn_violations, fp_violations = _check_fixture(fid, fx, spec)
            verdict = "PASS" if all_pass else "FAIL"

            if fn_violations:
                print(f"    FALSE NEGATIVES:")
                for v in fn_violations:
                    print(f"      - FN: {v}")
                all_fn.extend([(fid, v) for v in fn_violations])
            if fp_violations:
                print(f"    FALSE POSITIVES:")
                for v in fp_violations:
                    print(f"      - FP: {v}")
                all_fp.extend([(fid, v) for v in fp_violations])

            print(f"    verdict:  {verdict}  (elapsed={elapsed:.1f}s)")
            print()

            results.append({
                "fid": fid,
                "group": group,
                "query": query,
                "spec": spec,
                "all_pass": all_pass,
                "fn_violations": fn_violations,
                "fp_violations": fp_violations,
                "elapsed": elapsed,
                "error": None,
                "actual_chapters_count": len(actual_chapters),
                "actual_total_word_count": spec.total_word_count,
                "actual_citation_style": spec.citation_style,
                "actual_elements_count": len(actual_elements),
            })

        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"    ERROR: {type(exc).__name__}: {exc}")
            print(f"    elapsed: {elapsed:.1f}s")
            print()
            results.append({
                "fid": fid,
                "group": group,
                "query": query,
                "spec": None,
                "all_pass": False,
                "fn_violations": [f"EXCEPTION: {type(exc).__name__}: {exc}"],
                "fp_violations": [],
                "elapsed": elapsed,
                "error": str(exc),
                "actual_chapters_count": 0,
                "actual_total_word_count": None,
                "actual_citation_style": None,
                "actual_elements_count": 0,
            })
            all_fn.append((fid, f"EXCEPTION: {type(exc).__name__}: {exc}"))

    # --- Summary table ---
    print("=" * 72)
    print("SUMMARY TABLE")
    print("=" * 72)
    header = (
        f"{'ID':<10} {'GRP':<5} {'CH':<4} {'WC':<7} {'CIT':<14} {'EL':<4} "
        f"{'RESULT':<8} {'SEC'}"
    )
    print(header)
    print("-" * 72)
    pass_count = 0
    total_elapsed = 0.0
    for r in results:
        verdict = "PASS" if r["all_pass"] else "FAIL"
        if r["all_pass"]:
            pass_count += 1
        total_elapsed += r["elapsed"]
        cit = str(r["actual_citation_style"])[:12] if r["actual_citation_style"] else "None"
        print(
            f"{r['fid']:<10} {r['group']:<5} {r['actual_chapters_count']:<4} "
            f"{str(r['actual_total_word_count']):<7} {cit:<14} "
            f"{r['actual_elements_count']:<4} {verdict:<8} {r['elapsed']:.1f}s"
        )

    print("-" * 72)
    print(f"\nSCORE: {pass_count}/{len(CASES)} fixtures 全欄位通過")
    print(f"TOTAL ELAPSED: {total_elapsed:.1f}s")
    print(f"TOTAL LLM CALLS (actual): {len(CASES)} low-tier")

    # FALSE NEGATIVE / FALSE POSITIVE 最終匯總
    print(f"\nFALSE NEGATIVES (should-抽中 but 沒抽中/抽錯): {len(all_fn)}")
    for fid, v in all_fn:
        print(f"  {fid}: {v}")

    print(f"\nFALSE POSITIVES (should-留空 but 腦補了): {len(all_fp)}")
    for fid, v in all_fp:
        print(f"  {fid}: {v}")

    # 硬門檻判定
    print()
    if not all_fn and not all_fp:
        print("HARD GATE: PASS — FALSE NEGATIVES == [] AND FALSE POSITIVES == []")
    else:
        print("HARD GATE: FAIL")
        if all_fn:
            print(f"  FN={len(all_fn)} violation(s) — prompt 抽取不足或欄位值錯誤")
        if all_fp:
            print(f"  FP={len(all_fp)} violation(s) — LLM 在無 spec 時腦補了格式需求")

    print(f"\n[COST NOTE] low-tier，1 call/fixture，~5-6 calls/輪；"
          f"精確用量請查 OpenAI 帳單。")
    print("[CEO NOTE] 補 adversarial case 的唯一入口：本檔上方 CASES.extend 區塊（勿動 fixtures）。")
    print("=" * 72)

    # --- Write results to docs/scratch ---
    _write_results_file(results, all_fn, all_fp, pass_count, model_low)

    return pass_count, len(CASES), all_fn, all_fp, results


def _write_results_file(results, all_fn, all_fp, pass_count, model_low):
    """結果寫 docs/scratch/format-spec-extraction-run{N}-results.md（UTF-8 明寫）。"""
    # 找 N（不覆蓋舊 run）
    scratch_dir = os.path.join(_REPO_ROOT, "docs", "scratch")
    n = 1
    while os.path.exists(
        os.path.join(scratch_dir, f"format-spec-extraction-run{n}-results.md")
    ):
        n += 1
    out_path = os.path.join(scratch_dir, f"format-spec-extraction-run{n}-results.md")

    lines = [
        f"# Format Spec Extraction Harness — Run {n} Results",
        f"",
        f"**Date:** 2026-06-12  ",
        f"**Model (low tier):** {model_low}  ",
        f"**Fixtures:** {len(results)}  ",
        f"**Calls:** {len(results)} low-tier (1 call/fixture)  ",
        f"**Cost note:** ~$0.02–0.05/輪；精確用量請查 OpenAI 帳單  ",
        f"**Gate:** 此 harness 燒真 LLM 錢，跑前須 CEO 點頭  ",
        f"",
        f"## Score: {pass_count}/{len(results)} PASS",
        f"",
        f"## FALSE NEGATIVES (should-抽中 but 沒抽中/抽錯): {len(all_fn)}",
    ]
    for fid, v in all_fn:
        lines.append(f"- {fid}: {v}")
    lines += [
        f"",
        f"## FALSE POSITIVES (should-留空 but 腦補了): {len(all_fp)}",
    ]
    for fid, v in all_fp:
        lines.append(f"- {fid}: {v}")

    lines += ["", "## Per-Fixture Dump", ""]
    for r in results:
        lines.append(f"### {r['fid']} [{r['group']}]")
        lines.append(f"**Query:** {r['query']}")
        lines.append(f"**Result:** {'PASS' if r['all_pass'] else 'FAIL'}  ")
        lines.append(f"**Elapsed:** {r['elapsed']:.1f}s  ")
        if r["error"]:
            lines.append(f"**ERROR:** {r['error']}  ")
        else:
            lines.append(f"**total_word_count:** {r['actual_total_word_count']}  ")
            lines.append(f"**citation_style:** {r['actual_citation_style']}  ")
            lines.append(f"**chapters ({r['actual_chapters_count']}):**  ")
            if r["spec"] and r["spec"].chapters:
                for ch in r["spec"].chapters:
                    lines.append(f"  - {ch.name} (word_target={ch.word_target})")
            else:
                lines.append("  (empty)")
            lines.append(f"**special_elements ({r['actual_elements_count']}):**  ")
            if r["spec"] and r["spec"].special_elements:
                for el in r["spec"].special_elements:
                    lines.append(f"  - type={el.type} target_chapter='{el.target_chapter}'")
            else:
                lines.append("  (empty)")
            if r["fn_violations"]:
                lines.append("**FALSE NEGATIVES:**  ")
                for v in r["fn_violations"]:
                    lines.append(f"  - {v}")
            if r["fp_violations"]:
                lines.append("**FALSE POSITIVES:**  ")
                for v in r["fp_violations"]:
                    lines.append(f"  - {v}")
        lines.append("")

    out_text = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out_text)
    print(f"\n[RESULTS] 寫入 {out_path}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pass_count, total, all_fn, all_fp, _ = asyncio.run(run_verification())
    if all_fn or all_fp:
        sys.exit(1)
