# 自動評審 runner — 邏輯測試報告（① 測 runner 程式邏輯本身）

> **範圍**：只測 runner 的**純程式邏輯**——判分、flag 守門、校準分類、案例載入、環境擷取、mock 對應。
> **成本**：$0（不呼叫任何 LLM）。
> **測試檔**：`code/python/eval/critic_eval/test_runner_logic.py`（stdlib unittest，可重複跑）。
> **跑法**：`../../venv/Scripts/python.exe -m unittest eval.critic_eval.test_runner_logic -v`

---

## 結果總表

**22 / 22 通過**（0.03s）。

| 測試群 | 題數 | 測什麼 |
|---|---|---|
| `TestScoreCase` | 8 | §5 判分規則：指控/守住/漏抓、硬傷一票否決、軟傷不否決 |
| `TestCompareBaseline` | 3 | §6.5 flag 守門：同環境報分差、不同環境拒絕比 |
| `TestLoadCases` | 2 | gold 6 案載入驗證、缺 `cases:` 報錯 |
| `TestFormatContextAndEnv` | 3 | 來源編號格式、空來源、mock 環境擷取 |
| `TestMockMappings` | 5 | mock 評審/critic 把人工標註映射到正確維度 |
| `TestFullMockPipeline` | 1 | 整條 mock pipeline 對 gold 6 案 = 6/6（$0 sanity gate） |

---

## 抓到並修掉的 bug

**mock critique 長度不足（47 字 < schema 下限 50 字）**
- `_mock_critic` 的示範評語只有 47 字，但真 `CriticReviewOutput` schema 規定 `critique` 至少 50 字——註解自己還寫「長度需 >= 50 字」卻沒做到。
- mock 模式回純 dict 不過 schema 驗證，所以現在不會爆，但這是潛在不一致：宣稱是合法 critic 輸出、卻低於 schema 下限。真要拿 mock 輸出去餵任何走 schema 的路徑就會失敗。
- **已修**：改成 74 字，並在測試中加驗「這個 critique 真的能通過 `CriticReviewOutput` schema」。

---

## 判分規則：yoyo 定案「改嚴」（2026-08）

**原本的寬鬆問題**：某維度被評審指控時，只要 critic 的 `status != PASS`（REJECT 或 WARN），該維度就算「守住」——不管 critic 有沒有真的講到那個問題。後果：一個「因為 A 理由 REJECT、但沒提 B 問題」的 critic，B 也被記成守住；極端下「什麼都 REJECT」的爛 critic 可拿滿分，讓回歸偵測失靈。

**已改為嚴格規則**：被指控的維度，要 critic **自己那個欄位真的有寫東西**（`source_issues` / `logical_gaps` 非空）才算守住；光是「有退回」不給分。反面也成立——欄位有寫、即使 `status=PASS` 也算守住（critic 確實在對的維度點名了問題）。

- 實作：`score_case` 的 `flagged = bool(critic.get(field))`。
- 測試釘住：`test_strict_reject_without_field_is_a_miss`、`test_strict_warn_without_field_is_a_miss`、`test_strict_field_present_but_status_pass_still_held`。

---

## 順帶記錄的兩個小觀察（無害）

1. **§5 的「邏輯用多數決」目前退化成單票**：每個維度只有一個評審，所以「多數決」＝那一票。要等未來同維度加到多個評審才有意義。
2. **`critic_ok = not hard_missed and not misses` 的 `not hard_missed` 是冗餘的**：`hard_missed` 是 `misses` 的子集，`not misses` 已涵蓋。不影響正確性，只是可精簡。

---

## 驗證的關鍵行為（節錄）

- **flag 守門有效**：基準 env 與本次 env 不一致時，`compare_baseline` 印「比對中止 / 不可比」，不會給出假的分差。（§6.5 老闆最強調的點）
- **硬傷一票否決**：grounding/fabrication 任一被指控而 critic 沒守住 → `hard_veto=True`、`critic_ok=False`。
- **軟傷（logic）漏抓也算失敗、但不觸發 veto**：`hard_veto=False`。
- **乾淨案例**：無評審指控 → `critic_ok=True`。
- **$0 sanity gate**：完美 critic + 完美評審跑真 gold 6 案 → 6/6。

---

## 現況

runner 純邏輯已被測試覆蓋並釘住；抓到的 critique 長度 bug 已修。下一步（要花錢）才是：拿掉 `--mock` 真打 LLM 校準三評審，或撈真實 session 填 Q4 回歸組。
