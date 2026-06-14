"""
Chain of Verification (CoV) Prompt Builder - Phase 2.

Contains prompts for:
1. Extracting verifiable claims from draft
2. Verifying claims against sources
"""

from typing import List, Dict, Any


class CoVPromptBuilder:
    """
    Builds prompts for Chain of Verification tasks.

    CoV Process:
    1. Extract verifiable claims (numbers, dates, entities, events)
    2. Verify each claim against available sources
    """

    def build_claim_extraction_prompt(self, draft: str) -> str:
        """
        Build prompt for extracting verifiable claims from draft.

        Args:
            draft: The research draft to extract claims from

        Returns:
            Prompt string for LLM claim extraction
        """
        return f"""你是 **事實宣稱提取器**。

你的任務是從研究報告中提取所有**可驗證的事實宣稱**。

---

## 提取目標

請識別並提取以下類型的宣稱：

### 1. 數字 (number)
- 金額（如：營收 100 億）
- 數量（如：5 奈米製程）
- 百分比（如：成長 20%）
- 排名（如：全球第二大）

### 2. 日期 (date)
- 具體日期（如：2024 年 3 月 15 日）
- 時間點（如：Q3、第三季度）
- 相對時間（如：三年前、去年）

### 3. 人名 (person)
- 具名人物（如：張忠謀、黃仁勳）
- 職稱+姓名（如：執行長 Peter Wennink）

### 4. 機構名 (organization)
- 公司名稱（如：台積電、NVIDIA）
- 政府機構（如：經濟部、SEC）
- 組織團體（如：半導體協會）

### 5. 事件 (event)
- 具體事件（如：法說會、財報發布）
- 決策行動（如：宣布投資、簽署合約）

### 6. 統計數據 (statistic)
- 市佔率（如：佔全球 60%）
- 比較數據（如：比去年增加 30%）

### 7. 引述 (quote)
- 直接引述的話語（如：「我們預計...」）

---

## 提取原則

1. **只提取可驗證的宣稱**：
   - 「台積電 2024 年 Q1 營收達 5,926 億元」（可驗證）
   - 「台積電是一家重要的公司」（主觀判斷，不可驗證）

2. **注意引用標記**：
   - 如果宣稱後面有引用標記如 [1]、[2]，請記錄對應的 `source_reference`
   - 例如：「根據財報 [3]，營收成長 20%」→ source_reference: 3

3. **標記歸屬實體（subject_entity）—— 防張冠李戴**：
   - 每個宣稱把數字/事實/行為歸屬給某個**主詞實體**（公司/機構/人/地名）時，
     在 `subject_entity` 欄位記下**該主詞**
   - 例如：「台鹽綠能建立了 12 組合作模式」→ subject_entity: "台鹽綠能"
   - 例如：「台泥嘉謙綠能已有 12 組養殖夥伴」→ subject_entity: "台泥嘉謙綠能"
   - **沒有明確主詞**（如純背景敘述、被動語態無主體）→ subject_entity: null
   - **務必原文照抄主詞**，不要自行簡化或補全（後續驗證會做別名比對）
   - **重要**：`subject_entity` 是**輔助歸因欄位，非 claim 是否可驗證的必要條件**。
     缺主詞（null）**不影響**該 claim 的可驗證性，也**不代表**該 claim 無法驗證——
     仍照常提取、照常驗證，只是不做逐實體歸屬比對。

4. **保留上下文**：
   - 在 `context` 欄位記錄宣稱前後的句子，幫助後續驗證

5. **不要重複**：
   - 相同的事實只提取一次

---

## 輸出格式

請**嚴格**按照 ClaimsList schema 輸出 JSON：

```json
{{
  "claims": [
    {{
      "claim": "台積電 2024 年 Q1 營收達 5,926 億元",
      "claim_type": "number",
      "source_reference": 3,
      "subject_entity": "台積電",
      "context": "在財報公告中指出，台積電 2024 年 Q1 營收達 5,926 億元，較去年同期成長..."
    }},
    {{
      "claim": "張忠謀於 1987 年創立台積電",
      "claim_type": "event",
      "source_reference": null,
      "subject_entity": "張忠謀",
      "context": "作為全球晶圓代工龍頭，台積電由張忠謀於 1987 年創立，開創了..."
    }}
  ],
  "extraction_notes": "共提取 X 個可驗證宣稱，其中 Y 個有明確來源引用"
}}
```

---

## 待分析的草稿

{draft}

---

現在，請提取所有可驗證的事實宣稱。
"""

    def build_claim_verification_prompt(
        self,
        claims: List[Dict[str, Any]],
        formatted_context: str
    ) -> str:
        """
        Build prompt for verifying claims against sources.

        Args:
            claims: List of extracted claims to verify
            formatted_context: Formatted source context with citation markers

        Returns:
            Prompt string for LLM claim verification
        """
        # Format claims for prompt
        def _fmt_claim(i, c):
            line = f"{i+1}. [{c.get('claim_type', 'unknown')}] {c.get('claim', '')}"
            if c.get('source_reference'):
                line += f" (引用 [{c.get('source_reference')}])"
            subj = c.get('subject_entity')
            # A-2: 顯式呈現歸屬主詞，供逐實體比對；None → 標明無主詞
            line += f"\n   歸屬主詞: {subj if subj else '（無明確主詞）'}"
            return line

        claims_str = "\n".join([_fmt_claim(i, c) for i, c in enumerate(claims)])

        return f"""你是 **事實驗證器**。

你的任務是驗證每個事實宣稱是否有來源支持。

---

## 驗證標準

對於每個宣稱，判斷其驗證狀態：

### VERIFIED (已驗證)
- 來源中有**明確**支持此宣稱的內容
- 數字/日期必須**完全匹配**或**語意等價**
  - 「5,926 億元」= 「592.6 億美元」（匯率換算後等價）
  - 「Q1」= 「第一季度」= 「1-3月」
- 人名/機構名必須**完全一致**

### UNVERIFIED (未驗證)
- 來源中**找不到**支持此宣稱的內容
- 宣稱中引用了某來源 [N]，但該來源內容**沒有**提到此事實
- 這不代表宣稱是錯的，只是無法從現有來源驗證

### CONTRADICTED (矛盾)
- 來源中有**明確反駁**此宣稱的內容
- 例如：宣稱「營收成長 20%」，但來源說「營收下滑 5%」
- 這是最嚴重的問題

### PARTIALLY_VERIFIED (部分驗證)
- 宣稱的**部分**內容有來源支持
- 例如：宣稱「張忠謀在 1987 年創立台積電並擔任首任董事長」
  - 來源支持「1987 年創立」
  - 來源未提及「首任董事長」

---

## 驗證原則

1. **嚴格比對**：
   - 數字必須一致（允許單位換算）
   - 日期必須一致（允許格式差異）
   - 不要「腦補」或「推測」

2. **標註來源**：
   - 驗證成功時，記錄支持的來源 ID
   - 驗證失敗時，說明原因

3. **歸屬實體必須一致（防張冠李戴）**：
   - 每個宣稱下方標有 **`歸屬主詞`**（subject_entity）。請以此主詞為準，
     在來源中尋找**該主詞**的對應陳述（而非只查數字是否出現）
   - 若數字/事實本身存在於來源、但來源中它歸屬於**不同實體 B**（B 與宣稱主詞 A 非同一實體），
     則判 **CONTRADICTED**，不可因數字找得到就判 VERIFIED
   - **重要**：「來源未明確否認」不足以判 VERIFIED；若來源把同一數字/事實歸於 B，
     宣稱卻歸於 A（A≠B），即視為矛盾，無需來源另行聲明「A 沒有此數字」
   - 例如：宣稱「台鹽綠能建立了 12 組合作模式」（歸屬主詞=台鹽綠能），
     來源說的是「台泥嘉謙綠能...已有 12 組養殖夥伴」→ 歸屬實體不同 → CONTRADICTED

   - **別名/簡稱容忍（防誤殺，務必遵守）**：
     - 同一實體的全名、簡稱、別名、子公司關係**視為同一實體**，**不可**判 CONTRADICTED
     - 例如：「台泥嘉謙綠能」與「台泥」、「台積電」與「TSMC」、「台灣積體電路製造公司」
       —— 指涉同一主體，歸屬一致
     - 只有當主詞指涉**明確不同的主體**（如台鹽綠能 vs 台泥嘉謙綠能，是兩家不同公司）
       才因歸屬不符判 CONTRADICTED
     - `歸屬主詞` 為「（無明確主詞）」時，不做歸屬比對，僅依數字/事實本身是否有來源支持判定

4. **保守判斷**：
   - 如果不確定，傾向標記為 UNVERIFIED
   - 只有明確矛盾才標記 CONTRADICTED

---

## 待驗證的宣稱

{claims_str}

---

## 可用的來源資料

{formatted_context}

---

## 輸出格式

請**嚴格**按照 CoVVerificationOutput schema 輸出 JSON：

```json
{{
  "results": [
    {{
      "claim": "原始宣稱內容",
      "status": "verified",
      "evidence": "來源 [3] 中提到：台積電 Q1 營收達 5,926 億元...",
      "source_id": 3,
      "explanation": "來源 [3] 的財報數據與宣稱完全一致",
      "confidence": "high"
    }},
    {{
      "claim": "原始宣稱內容",
      "status": "unverified",
      "evidence": null,
      "source_id": null,
      "explanation": "現有來源中未找到關於此數據的記載",
      "confidence": "medium"
    }}
  ],
  "summary": "驗證結果摘要：共 X 個宣稱，Y 個已驗證，Z 個未驗證，W 個矛盾",
  "verified_count": Y,
  "unverified_count": Z,
  "contradicted_count": W
}}
```

---

現在，請逐一驗證每個宣稱。
"""

    def build_verification_summary_for_critic(
        self,
        verification_output: Dict[str, Any]
    ) -> str:
        """
        Build a summary of verification results for Critic's review prompt.

        Args:
            verification_output: The CoVVerificationOutput dict

        Returns:
            Formatted summary string to append to Critic prompt
        """
        results = verification_output.get("results", [])
        verified = verification_output.get("verified_count", 0)
        unverified = verification_output.get("unverified_count", 0)
        contradicted = verification_output.get("contradicted_count", 0)
        summary = verification_output.get("summary", "")

        # Build issue list
        issues = []
        for r in results:
            status = r.get("status", "")
            if status == "unverified":
                issues.append(f"- [未驗證] {r.get('claim', '')}: {r.get('explanation', '')}")
            elif status == "contradicted":
                issues.append(f"- [矛盾] {r.get('claim', '')}: {r.get('explanation', '')}")

        issues_str = "\n".join(issues) if issues else "（無問題）"

        return f"""
---

## Chain of Verification (CoV) 驗證結果

事實宣稱驗證已完成，結果如下：

### 統計
- **已驗證**: {verified} 個
- **未驗證**: {unverified} 個
- **矛盾**: {contradicted} 個

### 摘要
{summary}

### 發現的問題

{issues_str}

### 審查指引

根據 CoV 結果：
- 若有**矛盾**的宣稱 → 應考慮 **REJECT**
- 若有 3 個以上**未驗證**的宣稱 → 應考慮 **WARN**
- 請在 `logical_gaps` 中列出未驗證/矛盾的宣稱
- 請在 `suggestions` 中建議如何修正這些問題

"""
