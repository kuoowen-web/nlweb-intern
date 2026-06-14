"""
Gate 內 CoV subject_entity（A-2 張冠李戴結構修法）建構層 unit。
落點理由：repo 根 tests/unit/test_cov.py 在壞掉的 legacy 套件（conftest ImportError），
不在 gate；本檔在 code/python/tests 套件內，從 code/python 跑可被 gate collect。
detection 能力（A-2 是否真被抓）unit mock 驗不到 → 掛 harness（Task 6）。
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.unit
class TestSubjectEntitySchema:
    """VerifiableClaim subject_entity 欄位建構層。"""

    def test_module_imports(self):
        """Task 0 collect gate：核心模組可 import（真測試 — 空類別不會被 pytest collect，
        本測試保證檔案進 gate；AR R2 Codex blocker 修正）。"""
        from reasoning.schemas_enhanced import VerifiableClaim, ClaimsList
        from reasoning.prompts import cov as cov_prompts
        assert VerifiableClaim is not None
        assert ClaimsList is not None
        assert cov_prompts is not None

    def test_verifiable_claim_subject_entity_field(self):
        """subject_entity 欄位：可賦值、未給時預設 None（向後相容）。"""
        from reasoning.schemas_enhanced import VerifiableClaim, ClaimType

        # 顯式給主詞
        claim_with = VerifiableClaim(
            claim="台鹽綠能建立了 12 組合作模式",
            claim_type=ClaimType.NUMBER,
            subject_entity="台鹽綠能",
        )
        assert claim_with.subject_entity == "台鹽綠能"

        # 不給主詞 → 預設 None（向後相容：既有呼叫端不傳此欄位仍能建構）
        claim_without = VerifiableClaim(
            claim="營收成長 20%",
            claim_type=ClaimType.STATISTIC,
        )
        assert claim_without.subject_entity is None


@pytest.mark.unit
class TestSubjectEntityExtraction:
    """抽取 dict 攜帶 + 抽取 prompt 指示。"""

    @pytest.mark.asyncio
    async def test_extract_verifiable_claims_carries_subject_entity(self):
        """抽取 dict 必須攜帶 subject_entity（修補抽取→驗證資訊斷層）。"""
        from reasoning.schemas_enhanced import ClaimsList, VerifiableClaim, ClaimType

        mock_claims = ClaimsList(
            claims=[
                VerifiableClaim(
                    claim="台鹽綠能建立了 12 組合作模式",
                    claim_type=ClaimType.NUMBER,
                    subject_entity="台鹽綠能",
                ),
                VerifiableClaim(
                    claim="營收成長 20%",
                    claim_type=ClaimType.STATISTIC,
                    # 無主詞 → None
                ),
            ]
        )

        # 構造對齊鄰近 test_cov_lr_integration.py 既有 pattern：直接 CriticAgent(handler)。
        # （plan 原稿 patch reasoning.agents.critic.CONFIG 失敗 — critic 模組無 module-level
        #  CONFIG 屬性，_extract_verifiable_claims 也不讀 CONFIG，故 patch 為不存在的目標。
        #  改用 proven 直接構造法，測試意圖/斷言不變。）
        from reasoning.agents.critic import CriticAgent

        agent = CriticAgent(MagicMock(query_params={}), timeout=30)
        agent.call_llm_validated = AsyncMock(
            return_value=(mock_claims, 0, False)
        )

        result = await agent._extract_verifiable_claims("draft")

        assert result[0]["subject_entity"] == "台鹽綠能"
        assert result[1]["subject_entity"] is None
        # 既有 key 仍在（不 regress）
        assert result[0]["claim"] == "台鹽綠能建立了 12 組合作模式"
        assert result[0]["claim_type"] == "number"

    def test_extraction_prompt_instructs_subject_entity(self):
        """抽取 prompt 必須指示 LLM 標記 subject_entity（含 JSON 範例 key）。"""
        from reasoning.prompts.cov import CoVPromptBuilder

        builder = CoVPromptBuilder()
        prompt = builder.build_claim_extraction_prompt("test draft")

        # 欄位名出現在 JSON schema 範例中
        assert "subject_entity" in prompt
        # 指示文字解釋「歸屬實體」概念與 null 規則
        assert "歸屬" in prompt
        assert "主詞" in prompt or "主體" in prompt
        # SF-null：抽取端明示 subject_entity 為輔助欄位，null 不影響可驗證性
        assert "輔助" in prompt or "不影響" in prompt


@pytest.mark.unit
class TestSubjectEntityVerificationPrompt:
    """驗證 prompt 消費 + 別名容忍。"""

    def test_verification_prompt_surfaces_subject_entity(self):
        """驗證 prompt 須結構化呈現 subject_entity + 含張冠李戴判定 + 別名容忍。"""
        from reasoning.prompts.cov import CoVPromptBuilder

        builder = CoVPromptBuilder()
        claims = [
            {"claim": "台鹽綠能建立了 12 組合作模式", "claim_type": "number",
             "source_reference": 1, "subject_entity": "台鹽綠能"},
            {"claim": "近七成不符核定計畫", "claim_type": "statistic",
             "source_reference": 1, "subject_entity": None},
        ]
        context = "[1] 台泥嘉謙綠能已有 12 組養殖夥伴"

        prompt = builder.build_claim_verification_prompt(claims, context)

        # (a) claim line 三件齊全：claim text + source ref + subject_entity 結構化標籤
        assert "台鹽綠能建立了 12 組合作模式" in prompt   # claim text
        assert "[1]" in prompt or "引用" in prompt          # source ref
        assert "歸屬主詞" in prompt                          # 結構化 label（非裸字串）
        assert "台鹽綠能" in prompt                          # 有主詞 claim 的主詞值
        # null claim 標明無主詞（SF-null：不可推 unverified）
        assert "（無明確主詞）" in prompt or "無明確主詞" in prompt

        # (b) 張冠李戴判定指示語：evidence 歸 B 但宣稱歸 A → CONTRADICTED
        assert "CONTRADICTED" in prompt
        assert "不同實體" in prompt or "歸屬於" in prompt    # 配錯主體判矛盾
        # null claim 不做歸屬比對的明示（防 SF-null 漂移）
        assert "不做歸屬比對" in prompt or "僅依數字" in prompt

        # (c) 別名容忍（防誤殺：簡稱/別名不算矛盾）
        assert "別名" in prompt or "簡稱" in prompt
        assert "視為同一實體" in prompt or "同一主體" in prompt
