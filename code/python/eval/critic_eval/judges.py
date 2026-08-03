"""
The three adversarial judges for the critic auto-eval harness.

Each judge inspects (query, sources, draft) from ONE angle and its job is to
FIND FAULTS the critic should have caught — not to give a score. A judge that
finds nothing is what lets the critic "pass" that dimension.

Design (see docs/specs/critic-eval-plan.md §2):
- 評審 1 grounding    : is every concrete claim actually supported by a source?
- 評審 2 fabrication  : are the cited sources real / matching? any invented facts?
- 評審 3 logic        : does the conclusion follow from the premises, no jumps?

Prompt language: Traditional Chinese (matches reasoning/prompts/*.py).
Code comments: English.

Each builder returns a prompt string; the runner feeds it to the LLM with
response_schema=JudgeVerdict. The optional critic_verdict lets a judge focus on
what the critic PASSed; leave it None during Q3 calibration (judge vs human on
the draft alone).
"""

from typing import List, Optional
from core.prompts import generate_boundary_token, wrap_content_with_boundary
from eval.critic_eval.schema import Source


def _format_sources(sources: List[Source]) -> str:
    """Render sources as a numbered, boundary-isolated block (SEC-6)."""
    lines = [f"[{s.id}] {s.text}" for s in sources]
    body = "\n".join(lines) if lines else "（無來源）"
    boundary = generate_boundary_token()
    return wrap_content_with_boundary(body, boundary)


def _shared_header(query: str, sources: List[Source], draft: str,
                   critic_verdict: Optional[str]) -> str:
    """Common context block shared by all three judges."""
    isolated_draft = wrap_content_with_boundary(draft, generate_boundary_token())
    verdict_line = ""
    if critic_verdict:
        verdict_line = (
            f"\n## Critic 對這篇草稿的判定\n\n"
            f"Critic 給了「{critic_verdict}」。你的工作是檢查這個判定有沒有放過該抓的問題。\n"
        )
    return f"""## 使用者的問題

{query}

## 可用來源（草稿只能根據這些內容，不得引用來源以外的資訊）

{_format_sources(sources)}

## 待審草稿

{isolated_draft}
{verdict_line}"""


# ---------------------------------------------------------------------------
# 評審 1 — grounding：有沒有根據
# ---------------------------------------------------------------------------
def build_grounding_judge_prompt(query: str, sources: List[Source], draft: str,
                                 critic_verdict: Optional[str] = None) -> str:
    role = """你是嚴格的「根據性審查員」。你的唯一任務是找出草稿裡**缺乏來源根據**的具體說法。

判準：草稿中每一個具體主張（數字、日期、人名、機構、事件、因果、比較、評價）都必須能在「可用來源」裡對得上。只要一句話講了來源沒有的具體內容，就是問題。

重要立場：
- 你的預設是**挑毛病**，不是打分數。逐句掃描，寧可嚴格。
- 每找到一個問題，必須舉出**具體證據**：抄下草稿原句、指出去哪條來源查、來源裡實際上寫了什麼（或根本沒寫）。
- 只抓「根據性」問題（來源沒有卻寫了）。引用格式、邏輯跳步不是你的守備範圍。
- 真的每句都有根據，才回 found_issue=false。
"""
    tail = """
## 輸出

回傳符合 JudgeVerdict schema 的 JSON：dimension 固定填 "grounding"；把每個沒根據的說法放進 issues（quote=草稿原句，reason=為何沒根據，evidence_check=對照了哪條來源、來源實際內容）。
"""
    return role + "\n" + _shared_header(query, sources, draft, critic_verdict) + tail


# ---------------------------------------------------------------------------
# 評審 2 — fabrication：有沒有編造 / 假引用
# ---------------------------------------------------------------------------
def build_fabrication_judge_prompt(query: str, sources: List[Source], draft: str,
                                   critic_verdict: Optional[str] = None) -> str:
    role = """你是嚴格的「編造與引用查核員」。你的唯一任務是找出草稿裡**編造的內容**與**對不上的引用**。

要抓兩類問題：
1. 假引用：草稿標了 [N]，但編號 N 的來源根本不存在，或內容跟該句講的對不上（張冠李戴）。
2. 憑空編造：草稿講了一個具體事實（數字、引述、事件），但**任何**一條來源都沒有這個內容。

重要立場：
- 你的預設是**挑毛病**，不是打分數。
- 每找到一個問題，必須舉證：抄下草稿原句與它標的引用編號、指出該編號來源實際寫什麼、說明為何對不上或為何是憑空捏造。
- 只抓「編造／引用」問題。單純措辭抽象、邏輯跳步不是你的守備範圍。
- 引用都真實對得上、沒有捏造，才回 found_issue=false。
"""
    tail = """
## 輸出

回傳符合 JudgeVerdict schema 的 JSON：dimension 固定填 "fabrication"；每個問題放進 issues（quote=草稿原句，reason=假引用還是編造、為什麼，evidence_check=該引用編號來源的實際內容）。
"""
    return role + "\n" + _shared_header(query, sources, draft, critic_verdict) + tail


# ---------------------------------------------------------------------------
# 評審 3 — logic：邏輯有沒有跳步
# ---------------------------------------------------------------------------
def build_logic_judge_prompt(query: str, sources: List[Source], draft: str,
                             critic_verdict: Optional[str] = None) -> str:
    role = """你是嚴格的「邏輯審查員」。你的唯一任務是找出草稿裡**推論跳步**的地方。

判準：草稿的每個結論，都必須能從它前面給的前提（與來源）合理推得。要抓的是：
- 前提只支持 A，卻直接跳到更強的結論 B（過度推論）。
- 因果宣稱缺中間環節（「因為 X 所以 Y」但沒交代 X 如何導致 Y）。
- 以偏概全、把個案講成通則、把相關講成因果。

重要立場：
- 你的預設是**挑毛病**，不是打分數。
- 每找到一個跳步，必須舉證：抄下草稿的結論句、指出它依賴哪個前提、說明中間缺了什麼環節。
- 只抓「邏輯跳步」。內容有沒有根據、引用真不真，不是你的守備範圍（那是另外兩位評審的事）。
- 推論都接得起來，才回 found_issue=false。
"""
    tail = """
## 輸出

回傳符合 JudgeVerdict schema 的 JSON：dimension 固定填 "logic"；每個跳步放進 issues（quote=草稿結論句，reason=缺了什麼推論環節，evidence_check=它依賴的前提／來源說到哪為止）。
"""
    return role + "\n" + _shared_header(query, sources, draft, critic_verdict) + tail


# Registry the runner iterates over. dimension -> builder.
JUDGES = {
    "grounding": build_grounding_judge_prompt,
    "fabrication": build_fabrication_judge_prompt,
    "logic": build_logic_judge_prompt,
}
