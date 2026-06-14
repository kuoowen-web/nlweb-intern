"""
F3 CoV-lite LR harness fixtures (Track F Task F4 補債).

三組分類：
- 組 A：應抓題（expected_cov_action in {"warn", "reject"}）
- 組 B：should-PASS 探針（expected_cov_action == "pass"，量 false-positive）
- 組 C：CEO 對抗題（預留空插槽）
- 組 D：真實量級（evidence_text ≤12000 字，從 lr_mock_bab_real 取材）

紀律：組 A/B/D 由設計者撰寫（存在 overfit 下界風險）；
組 C 必須由設計者以外（CEO）出題填補 —— **CEO 對抗題的唯一 append 入口在
harness（verify_cov_lr.py）的 CASES.extend 區，不要動本檔的 ALL_COV_FIXTURES**
（理由：harness 才是跑 LLM 的地方，且本檔 sanity test 對基礎清單做結構檢查）。

每筆 fixture 格式：
{
    "section_content": str,        # CoV 的 draft 輸入
    "evidence_text": str,          # CoV 的 formatted_context 輸入
    "expected_cov_action": str,    # "pass" / "warn" / "reject"（小寫；CoV 本身回 counts 不回 verdict 字串，harness _cov_result_to_action 換算）
    "fabrication_type": str,       # "numeric" / "attribution" / "precision" / "causal" / "faithful" / "paraphrase"
    "explanation": str,            # 為何此案應被 flag（或 should-PASS 理由）
}
"""

# ---------------------------------------------------------------------------
# 組 A：應抓題（短文本，快速基線驗測）
# ---------------------------------------------------------------------------

# A-1：捏造數字 — evidence 只說「大約三成」，section 給出精確 29.8%
FIXTURE_A1_PRECISION_INFLATION = {
    "section_content": (
        "根據審計部報告，漁電共生案場中約有 29.8% 不符合核定計畫 [1]，"
        "顯示政策執行落差顯著。"
    ),
    "evidence_text": (
        "[1] 審計部報告揭「漁電共生」驚人假象：近7成不符核定計畫\n"
        "審計部報告指出，近七成漁電共生案場不符合核定計畫內容，政府對業者的督導機制有待加強。"
    ),
    "expected_cov_action": "reject",
    "fabrication_type": "precision",
    "explanation": (
        "evidence 說「近七成不符」（≈70%），section 卻說「29.8% 不符」—— 方向相反且精度灌水。"
        "CoV 應偵測到 claimed 29.8% 與 evidence 「近七成」矛盾 → REJECT。"
    ),
}

# A-2：張冠李戴 — 數字真在 evidence 但配錯主體（最難類）
FIXTURE_A2_ATTRIBUTION_ERROR = {
    "section_content": (
        "台泥嘉謙綠能在 2018 年投入漁電共生，目前已有 12 組養殖夥伴成功合作 [1]。"
        "台鹽綠能同年也建立了 12 組類似合作模式，成為業界標竿。"
    ),
    "evidence_text": (
        "[1] 台泥漁電場撤照案\n"
        "台泥嘉謙綠能表示，2018年投入嘉義漁電共生案場，已有12組養殖夥伴成功開創漁電共生模式，"
        "將持續投入低碳養殖技術輔導、建立生態監測數據平台。"
    ),
    "expected_cov_action": "reject",
    "fabrication_type": "attribution",
    "explanation": (
        "「12 組養殖夥伴」是台泥的數字，section 把同樣數字安到台鹽綠能身上。"
        "evidence 完全沒提台鹽綠能 12 組的主張 → CoV 應偵測 attributed 主體錯誤 → REJECT。"
        "此為張冠李戴最難類：數字真在 evidence，但配的主體不同。"
    ),
}

# A-3：WARN 閾值專測題 —— 塞 >=3 個各自無據的 claim，使 unverified_count 真能達 >=3
# （adversarial review SF-2：單一弱因果在 unverified>=3 閾值下大概率只 1-2 claim → 測不到
#  WARN，MISS 會被誤讀成 detection 缺口。此題刻意塞 3+ 個 evidence 沒有的 unsupported claim，
#  使這題真正覆蓋 WARN 路徑。這是 plan 唯一的 WARN-expected 題，砍掉等於 WARN 閾值零覆蓋。）
FIXTURE_A3_WARN_THRESHOLD = {
    "section_content": (
        "漁電共生政策失敗的根本原因在於業者缺乏農業專業能力 [1]。"
        "此外，主管機關每年編列超過新台幣 50 億元的專案稽核預算 [1]，"
        "並已在全台設立 18 個區域輔導中心協助漁民轉型 [1]，"
        "且根據內部評估，導入 AI 監測後違規率可下降至 5% 以下 [1]。"
    ),
    "evidence_text": (
        "[1] 審計部報告揭「漁電共生」驚人假象\n"
        "審計部報告指出近七成漁電共生案場不符核定計畫。"
        "部分養殖漁民抱怨土地租金飆漲，光電業者挾帶資金優勢進入市場，"
        "迫使傳統養殖被排擠。布袋地區業者控訴光電業者優先調度抽排水，造成魚群死亡。"
    ),
    "expected_cov_action": "warn",
    "fabrication_type": "causal",
    "explanation": (
        "section 含 4 個 evidence 完全未支撐的 claim：(1)「缺乏農業專業能力是根本原因」"
        "（evidence 列多重原因，未支持此單一因果）、(2)「50 億稽核預算」、(3)「18 個區域輔導中心」、"
        "(4)「AI 監測違規率降至 5% 以下」—— 後三者 evidence 完全沒提（不是矛盾，是無據）。"
        "預期 unverified_count >= 3 → WARN（非 contradicted → 非 REJECT）。"
        "本題的設計目的是讓 WARN 閾值真能被觸發；harness 會印 raw unverified_count 供判讀，"
        "若 unverified < 3 顯示為 PASS，需區分『閾值未達』vs『detection 失敗』（看 raw counts）。"
    ),
}

# A-4：捏造政策數字
FIXTURE_A4_POLICY_NUMBER_FABRICATION = {
    "section_content": (
        "日本農林水產省規定，農電共生的農作物產量不能低於前三年平均的六成 [1]，"
        "違反者三年後許可即終止。"
    ),
    "evidence_text": (
        "[1] 日光能否共享？之三：新農業的實驗場\n"
        "日本農林水產省規定，農電共生的農作物產量不能低於平均的八成，"
        "地方的農業委員會每年會審查農業計畫，也會到現場查核。"
        "由於營農型光電每三年就要重新申請核准，如果農民不做改善，三年一到就會終止許可。"
    ),
    "expected_cov_action": "reject",
    "fabrication_type": "numeric",
    "explanation": (
        "evidence 明確說「不能低於平均的八成」，section 說「六成」—— 數字直接矛盾。"
        "CoV 應偵測此數字衝突 → REJECT。"
    ),
}

# ---------------------------------------------------------------------------
# 組 B：should-PASS 探針（量 false-positive，expected_cov_action = "pass"）
# ---------------------------------------------------------------------------

# B-1：忠實複述 evidence 核心事實
FIXTURE_B1_FAITHFUL_RESTATEMENT = {
    "section_content": (
        "根據報導，台泥嘉謙綠能在 2018 年投入嘉義漁電共生案場，"
        "目前已有 12 組養殖夥伴成功建立合作模式 [1]。"
    ),
    "evidence_text": (
        "[1] 台泥漁電場撤照案\n"
        "台泥嘉謙綠能表示，2018年投入嘉義漁電共生案場，已有12組養殖夥伴成功開創漁電共生模式，"
        "將持續投入低碳養殖技術輔導、建立生態監測數據平台，以達成綠電發電年減碳量1萬5000公噸的目標。"
    ),
    "expected_cov_action": "pass",
    "fabrication_type": "faithful",
    "explanation": (
        "section 完全忠實複述 evidence：公司名稱、年份（2018）、12 組養殖夥伴均正確。"
        "CoV 若 WARN/REJECT = false positive，表示 CoV 過度拒絕。"
    ),
}

# B-2：合理同義改寫（「顯著提升」↔「明顯改善」不算 fabrication）
FIXTURE_B2_SYNONYMOUS_PARAPHRASE = {
    "section_content": (
        "台南麻豆地區居民在光電業者提出漁電共生申設計畫後，"
        "迅速組成自救會進行抗議，擔憂被太陽能板包圍 [1]。"
    ),
    "evidence_text": (
        "[1] 麻豆居民反光電 怒吼「人不如鳥」\n"
        "台南有光電業者以漁電共生方式在麻豆北勢、港尾里計畫設置28公頃太陽光電發電廠，"
        "當地住戶2周前得知消息立刻組成自救會，住戶憂心遭光電板包圍，「難道人不如鳥嗎？」"
    ),
    "expected_cov_action": "pass",
    "fabrication_type": "paraphrase",
    "explanation": (
        "section 是 evidence 的合理改寫：「迅速組成自救會」≈ evidence「立刻組成自救會」；"
        "「擔憂被太陽能板包圍」≈ evidence「憂心遭光電板包圍」。"
        "未捏造任何數字或事實。CoV 若 WARN/REJECT = false positive。"
    ),
}

# B-3：別名/簡稱改寫 —— 主詞用簡稱但歸屬正確，CoV 不可因別名誤判 CONTRADICTED
# （誤殺防護：Task 4 別名容忍規則的對抗探針。evidence 用全名「台泥嘉謙綠能」，
#  section 用簡稱「台泥」，數字與事實完全一致 → 應 PASS，不可因主詞字面不同判矛盾。）
FIXTURE_B3_ALIAS_REWRITE = {
    "section_content": (
        "台泥在 2018 年投入嘉義漁電共生案場，目前已有 12 組養殖夥伴成功合作 [1]。"
    ),
    "evidence_text": (
        "[1] 台泥漁電場撤照案\n"
        "台泥嘉謙綠能表示，2018年投入嘉義漁電共生案場，已有12組養殖夥伴成功開創漁電共生模式，"
        "將持續投入低碳養殖技術輔導、建立生態監測數據平台。"
    ),
    "expected_cov_action": "pass",
    "fabrication_type": "faithful",
    "explanation": (
        "section 主詞用簡稱「台泥」，evidence 用全名「台泥嘉謙綠能」—— 指涉同一主體。"
        "年份（2018）、12 組養殖夥伴均忠實對應。CoV 應依別名容忍規則判 PASS，"
        "若因主詞字面不同判 CONTRADICTED = false positive（過嚴誤殺）。"
        "此題是 Task 4 別名容忍規則的對抗探針，與 A-2（真張冠李戴）成對照組。"
    ),
}

# ---------------------------------------------------------------------------
# 組 C：CEO 對抗題（不在本檔！唯一 append 入口在 harness verify_cov_lr.py 的
#       CASES.extend 區，照 verify_l3_critic.py 慣例。）
# !! 此組必須由 CEO 補題，不得由設計者填入（設計者自測必 overfit）!!
# !! 不要在本檔宣告 CEO 清單變數 —— 既往 plan 的 CEO_ADVERSARIAL_CASES 是
#    從未被任何清單消費的死碼，且若往 ALL_COV_FIXTURES append 會撞 sanity 斷言。!!
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 組 D：真實量級 —— evidence_text 由真 render_grounding_evidence_view 產生
# ---------------------------------------------------------------------------
# 保真度根治（adversarial review SF-3）：不再手刻近似字串（手刻版缺 production 的
# trailing claim bullet 行 + 缺「只渲染有 renderable claim 的 eid」過濾，格式失真）。
# 改成 load evidence_pool.json → deserialize_evidence_pool → 呼叫真 renderer。
import os as _os
from reasoning.schemas_live import (
    deserialize_evidence_pool,
    render_grounding_evidence_view,
)

_POOL_PATH = _os.path.join(
    _os.path.dirname(__file__), "..", "..",
    "fixtures", "lr_mock_bab_real", "evidence_pool.json",
)
# 註：實作時依實際 repo 相對路徑調整（tests/fixtures/lr_mock_bab_real/evidence_pool.json）。
# !! deserialize_evidence_pool(s: str) 親驗簽名吃「JSON 字串」非 parsed dict !!
#    故傳檔案原文 _f.read()，不要先 json.load。回傳 Dict[int, EvidencePoolEntry]（key 已轉 int）。

with open(_POOL_PATH, encoding="utf-8") as _f:
    _EVIDENCE_POOL = deserialize_evidence_pool(_f.read())

# 給最小 evidence_usage：renderer 只渲染「有 renderable claim 的 eid」(critic_status != REJECT)。
# 對所選 eid 各塞一個 PASS claim 使其被渲染（claim 文字取自該 evidence snippet 重點）。
def _claim(text):
    return {"reasoning_type": "直接引用", "confidence": "high",
            "claim": text, "critic_status": "PASS"}

_SUBSET_EIDS = [1, 3, 6, 23, 36]
_EVIDENCE_USAGE_SUBSET = {
    1: [_claim("麻豆居民組自救會反 28 公頃漁電共生案場")],
    3: [_claim("台南地院以契約未成立駁回養殖戶 566 萬求償")],
    6: [_claim("2017 年農業部修訂規定禁止網室設置光電")],
    23: [_claim("審計部報告近七成漁電共生案場不符核定計畫")],
    36: [_claim("台泥嘉謙綠能 2018 投入嘉義案場已有 12 組養殖夥伴")],
}

# 真實量級子集（5 筆）evidence_text —— 真 renderer 輸出，格式與 production 一致
_REAL_EVIDENCE_SUBSET = render_grounding_evidence_view(
    chapter_eids=_SUBSET_EIDS,
    evidence_usage=_EVIDENCE_USAGE_SUBSET,
    evidence_pool={k: _EVIDENCE_POOL[k] for k in _SUBSET_EIDS if k in _EVIDENCE_POOL},
    prior_grounded_entities=[],
    char_budget=12000,
)

# full-pool（逼近 12000 budget）evidence_text —— 量級/穩定性 smoke（D-3 用）
_REAL_EVIDENCE_FULLPOOL = render_grounding_evidence_view(
    chapter_eids=list(_EVIDENCE_POOL.keys()),
    evidence_usage={
        k: [_claim(getattr(_EVIDENCE_POOL[k], "title", "") or "")]
        for k in _EVIDENCE_POOL
    },
    evidence_pool=_EVIDENCE_POOL,
    prior_grounded_entities=[],
    char_budget=12000,
)

# D-1：真實量級 evidence（子集）+ 應抓題（數字灌水）
FIXTURE_D1_REAL_SCALE_DETECT = {
    "section_content": (
        "根據審計部調查，全台近八成漁電共生案場不符核定計畫 [23]，"
        "且布袋地區有鰻魚養殖業者長達 60 年以上的經驗 [23]。"
        "台泥嘉謙綠能在 2018 年投入嘉義案場，已有 15 組養殖夥伴合作 [36]。"
    ),
    "evidence_text": _REAL_EVIDENCE_SUBSET,
    "expected_cov_action": "reject",
    "fabrication_type": "numeric",
    "explanation": (
        "多處數字灌水或捏造：(1) 審計部說「近七成不符」，section 說「近八成」；"
        "(2) evidence 說「50年」鰻魚養殖經驗，section 說「60年以上」；"
        "(3) evidence 說「12組養殖夥伴」，section 說「15組」。"
        "真實量級（5 筆 evidence，真 renderer 輸出）驗 CoV 在接近 production 的 context 下仍能偵測。"
    ),
}

# D-2：真實量級 evidence（子集）+ should-PASS 探針
FIXTURE_D2_REAL_SCALE_PASS = {
    "section_content": (
        "台南麻豆地區居民反對光電業者以漁電共生方式設置 28 公頃太陽能發電廠 [1]，"
        "並組成自救會抗議。台鹽綠能與養殖戶的合約糾紛案，"
        "台南地方法院以契約未成立為由駁回養殖戶 566 萬的求償 [3]。"
        "政府從 2013 年起開放設施型農電共生，"
        "但 2017 年農業部修訂規定，禁止網室設置光電 [6]。"
    ),
    "evidence_text": _REAL_EVIDENCE_SUBSET,
    "expected_cov_action": "pass",
    "fabrication_type": "faithful",
    "explanation": (
        "所有事實均忠實對應 evidence："
        "28 公頃（evidence [1] 有）、566 萬求償、台南地方法院駁回（evidence [3] 有）、"
        "2013 年起開放、2017 年修訂、禁止網室（evidence [6] 有）。"
        "CoV 若 WARN/REJECT = false positive。"
        "此為真實量級 should-PASS 探針，驗 CoV 不因長 context 而誤判。"
    ),
}

# D-3：full-pool（逼近 12000 budget）量級/穩定性 smoke
# section 含一個明確捏造數字確保有 verifiable claim；重點不在 detect 結果而在
# 「CoV 在接近 budget 上限的長 context 下不 crash / 不空判 / 記錄 elapsed」。
FIXTURE_D3_FULLPOOL_SMOKE = {
    "section_content": (
        "綜合全案報導，台泥嘉謙綠能 2018 年投入嘉義案場已有 99 組養殖夥伴合作 [36]，"
        "顯示漁電共生模式快速擴張。"
    ),
    "evidence_text": _REAL_EVIDENCE_FULLPOOL,
    "expected_cov_action": "reject",
    "fabrication_type": "numeric",
    "explanation": (
        "full-pool render（逼近 12000 char_budget）。section 把「12組」捏造成「99組」應被抓。"
        "本題主目的是量級/穩定性 smoke：驗 CoV 在接近 production budget 上限的長 context 下"
        "不 crash、不空判、可記 elapsed。若因量級導致 degraded（verification_status=unverified），"
        "harness 應記 ERROR 而非 PASS（見 harness _cov_result_to_action degraded 防護）。"
    ),
}

# ---------------------------------------------------------------------------
# ALL_FIXTURES：harness 用，完整清單
# ---------------------------------------------------------------------------
# 組 C（CEO 對抗題）不在此清單 —— CEO 唯一 append 入口在 harness CASES.extend 區。
ALL_COV_FIXTURES = [
    ("COV-A-1", FIXTURE_A1_PRECISION_INFLATION),
    ("COV-A-2", FIXTURE_A2_ATTRIBUTION_ERROR),
    ("COV-A-3", FIXTURE_A3_WARN_THRESHOLD),
    ("COV-A-4", FIXTURE_A4_POLICY_NUMBER_FABRICATION),
    ("COV-B-1", FIXTURE_B1_FAITHFUL_RESTATEMENT),
    ("COV-B-2", FIXTURE_B2_SYNONYMOUS_PARAPHRASE),
    ("COV-B-3", FIXTURE_B3_ALIAS_REWRITE),
    ("COV-D-1", FIXTURE_D1_REAL_SCALE_DETECT),
    ("COV-D-2", FIXTURE_D2_REAL_SCALE_PASS),
    ("COV-D-3", FIXTURE_D3_FULLPOOL_SMOKE),
]


# ---------------------------------------------------------------------------
# Sanity tests（不跑 LLM，只驗 fixture 格式）
# ---------------------------------------------------------------------------
def test_all_cov_fixtures_well_formed():
    """所有 fixture 格式正確，不呼叫 LLM。"""
    # SF-1：用 >= 而非 ==，避免 CEO 補題（在 harness append）時若有人誤動本清單被懲罰；
    # 同時分組驗結構（4 組A + 2 組B + 3 組D 基礎）。
    assert len(ALL_COV_FIXTURES) >= 9, (
        f"Expected >= 9 base fixtures (4 組A + 2 組B + 3 組D), got {len(ALL_COV_FIXTURES)}"
    )
    group_a = [fid for fid, _ in ALL_COV_FIXTURES if fid.startswith("COV-A")]
    group_b = [fid for fid, _ in ALL_COV_FIXTURES if fid.startswith("COV-B")]
    group_d = [fid for fid, _ in ALL_COV_FIXTURES if fid.startswith("COV-D")]
    assert len(group_a) >= 4 and len(group_b) >= 2 and len(group_d) >= 3, (
        f"組分布不符：A={group_a} B={group_b} D={group_d}"
    )
    for fid, fx in ALL_COV_FIXTURES:
        assert isinstance(fx, dict), f"{fid} not dict"
        assert "section_content" in fx and isinstance(fx["section_content"], str), \
            f"{fid} missing section_content"
        assert "evidence_text" in fx and isinstance(fx["evidence_text"], str), \
            f"{fid} missing evidence_text"
        assert fx["evidence_text"].strip(), f"{fid} evidence_text empty"
        assert fx["expected_cov_action"] in {"pass", "warn", "reject"}, \
            f"{fid} invalid expected_cov_action: {fx['expected_cov_action']}"
        assert fx["fabrication_type"] in {
            "numeric", "attribution", "precision", "causal", "faithful", "paraphrase"
        }, f"{fid} invalid fabrication_type: {fx['fabrication_type']}"
        assert "explanation" in fx and fx["explanation"], f"{fid} missing explanation"


def test_real_scale_evidence_is_real_renderer_output():
    """組 D evidence_text 由真 render_grounding_evidence_view 產生（非手刻字串）。

    驗：(a) 含 production 格式標記 `### [`；(b) 子集量級合理；(c) full-pool 比子集大
    （逼近 budget），證明用的是真 renderer 而非常數字串。
    """
    d_map = {fid: fx for fid, fx in ALL_COV_FIXTURES if fid.startswith("COV-D")}
    for fid, fx in d_map.items():
        ev = fx["evidence_text"]
        assert "### [" in ev, f"{fid} evidence_text 缺 render 標記，可能不是真 renderer 輸出"
        assert len(ev) > 300, f"{fid} evidence_text 過短 ({len(ev)})"
    # full-pool（D-3）應明顯大於子集（D-1）——證明真 renderer 依 pool 規模變化
    if "COV-D-1" in d_map and "COV-D-3" in d_map:
        assert len(d_map["COV-D-3"]["evidence_text"]) >= len(d_map["COV-D-1"]["evidence_text"]), (
            "full-pool render 應 >= 子集 render；若相等可能 renderer 未生效"
        )


def test_should_pass_probes_exist():
    """至少有 2 個 should-PASS 探針（expected_cov_action == 'pass'），包含真實量級。"""
    pass_probes = [fid for fid, fx in ALL_COV_FIXTURES if fx["expected_cov_action"] == "pass"]
    assert len(pass_probes) >= 2, (
        f"Need at least 2 should-PASS probes, got {len(pass_probes)}: {pass_probes}"
    )
    # 至少一個真實量級 should-PASS
    real_scale_passes = [fid for fid in pass_probes if fid.startswith("COV-D")]
    assert len(real_scale_passes) >= 1, (
        "Need at least 1 real-scale should-PASS probe (COV-D-*)"
    )
