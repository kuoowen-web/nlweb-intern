"""IT-1 (full-scan-2026-07 批5)：cloud_embed .done gate 對稱化守護。

背景（findings AS/IT-1，三席同抓 in-house + Codex#F1 + Gemini#F4）
----------------------------------------------------------------
`cloud_embed.main()` 舊行為：對每個「未 raise」的 TSV 無條件寫 `.done`，
**完全不看** stats 裡 skipped/failed。parse 壞 JSON / 短文一律回 None 算
skipped 不影響寫 done。與 `bulk_load.py` 已硬化的 `.done` gate
（`if stats["errors"] == 0` 才寫）**不對稱**。

後果：暫時性大量 parse None 的 TSV（例：上游一次性資料損壞、編碼問題）會被
永久 checkpoint 跳過 → 該批文章永不 embed（silent 漏資料）。

本 suite 釘住對稱化後的三態 gate（`_should_mark_done` 純函式）+ `main()`
端到端行為：
  - 正常批（success>0, failed==0）→ 寫 .done。
  - 全 skip 批（success==0）→ **不寫** .done + loud warning（讓下輪重試）。
  - 錯誤批（failed>0）→ **不寫** .done + loud warning。

完全不碰真 TSV / GCS / DB / GPU model —— process_tsv 與 get_model 皆 mock。
"""
from pathlib import Path
from unittest.mock import patch

import pytest

import indexing.cloud_embed as ce


# ── 純函式：三態 gate 判準 ────────────────────────────────────────────

def test_should_mark_done_normal_batch():
    """正常批：有文章成功 embed 且無失敗 → 寫 done。"""
    assert ce._should_mark_done({"success": 5, "failed": 0, "skipped": 2, "chunks": 12}) is True


def test_should_mark_done_all_skipped_batch():
    """全 skip 批：success==0（全部 parse None / 短文）→ 不寫 done。

    這是 IT-1 的核心缺口：舊 code 對此批仍寫 done → 永久漏 embed。
    """
    assert ce._should_mark_done({"success": 0, "failed": 0, "skipped": 8, "chunks": 0}) is False


def test_should_mark_done_error_batch():
    """錯誤批：failed>0（有文章處理失敗）→ 不寫 done，讓下輪重試。"""
    assert ce._should_mark_done({"success": 3, "failed": 2, "skipped": 1, "chunks": 7}) is False


def test_should_mark_done_empty_batch():
    """空批（空檔 / 全空行，success==0 且 skipped==0）→ 不寫 done。

    保守方向：沒有任何文章真的被 embed 就不宣告完成（重試無害）。
    """
    assert ce._should_mark_done({"success": 0, "failed": 0, "skipped": 0, "chunks": 0}) is False


# ── main() 端到端：.done 檔寫入行為 ──────────────────────────────────

def _run_main_with_stats(tmp_path: Path, per_file_stats: dict, monkeypatch) -> set[str]:
    """跑 main()，用 mock 的 process_tsv 回傳指定 stats，回傳寫進 .done 的 basename 集合。

    per_file_stats: {tsv_basename: stats_dict}。
    """
    tsv_dir = tmp_path / "tsv"
    out_dir = tmp_path / "out"
    tsv_dir.mkdir()
    out_dir.mkdir()

    for name in per_file_stats:
        (tsv_dir / name).write_text("dummy\n", encoding="utf-8")

    def fake_process_tsv(tsv_path, output_dir, batch_size=32):
        return per_file_stats[Path(tsv_path).name]

    monkeypatch.setattr(ce, "process_tsv", fake_process_tsv)
    monkeypatch.setattr(ce, "get_model", lambda: None)  # 不載真 GPU model
    monkeypatch.setattr(ce.sys, "argv", ["cloud_embed.py", str(tsv_dir), str(out_dir)])

    ce.main()

    done_file = out_dir / ".done"
    if not done_file.exists():
        return set()
    return {line.strip() for line in done_file.read_text(encoding="utf-8").splitlines() if line.strip()}


def test_main_writes_done_only_for_healthy_batches(tmp_path, monkeypatch):
    """main() 只對正常批寫 .done；全 skip 批與錯誤批不寫（下輪重試）。"""
    per_file_stats = {
        "good.tsv": {"success": 4, "failed": 0, "skipped": 0, "chunks": 9},
        "allskip.tsv": {"success": 0, "failed": 0, "skipped": 6, "chunks": 0},
        "errored.tsv": {"success": 2, "failed": 3, "skipped": 0, "chunks": 5},
    }
    done = _run_main_with_stats(tmp_path, per_file_stats, monkeypatch)

    assert "good.tsv" in done, "正常批應寫 .done"
    assert "allskip.tsv" not in done, "全 skip 批不可寫 .done（IT-1 核心）"
    assert "errored.tsv" not in done, "錯誤批不可寫 .done"


def test_main_skip_only_batch_logs_warning(tmp_path, monkeypatch):
    """全 skip 批不寫 done 時必須 loud log（no silent fail）。

    直接 monkeypatch `ce.logger.warning` 攔截呼叫（不用 pytest caplog、不掛
    logging.Handler）——兩者都受全域 logging state 影響：caplog 依賴 propagate
    （會被前面測試初始化的 async LoggerUtility 框架污染，lessons-general Class A），
    掛 Handler 也會被前面測試殘留的 `logging.disable(...)` / level 調整壓掉。
    直接攔 warning 方法呼叫對 ordering 完全免疫（不經全域 logging 過濾）。
    """
    warn_calls: list[str] = []
    real_warning = ce.logger.warning

    def _spy_warning(msg, *args, **kwargs):
        warn_calls.append(str(msg))
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(ce.logger, "warning", _spy_warning)

    per_file_stats = {
        "allskip.tsv": {"success": 0, "failed": 0, "skipped": 5, "chunks": 0},
    }
    done = _run_main_with_stats(tmp_path, per_file_stats, monkeypatch)

    assert "allskip.tsv" not in done, "全 skip 批不可寫 .done"
    assert any("allskip.tsv" in m for m in warn_calls), (
        "全 skip 批不寫 done 必須留 loud warning 指出檔名（no silent fail）"
    )


# ── 隱性 contract 鎖：cloud_embed warning ↔ orchestrator grep 判準 ──────

import re


def _extract_orchestrator_skip_token() -> str:
    """從 scripts/indexing_orchestrator.sh 抽出 validate 用來偵測 skip-only 批的
    grep substring（`if "<token>" in logtxt:` 那行的字面值）。

    這是兩側隱性 contract 的『orchestrator 端真相』——直接讀原檔而非硬編碼常數，
    才能在 orchestrator 改文案時也讓本測試紅（否則只鎖住 cloud_embed 單邊）。
    """
    # test 檔在 code/python/tests/unit/indexing/ →
    # parents[0]=indexing [1]=unit [2]=tests [3]=python [4]=code [5]=repo 根
    repo_root = Path(__file__).resolve().parents[5]
    sh_path = repo_root / "scripts" / "indexing_orchestrator.sh"
    assert sh_path.exists(), f"找不到 indexing_orchestrator.sh：{sh_path}"
    text = sh_path.read_text(encoding="utf-8")
    # 抓 `fail("embed log 含 SKIP done-mark:...` 前的判斷行：if "SKIP done-mark:" in logtxt:
    m = re.search(r'if\s+"([^"]*done-mark[^"]*)"\s+in\s+logtxt', text)
    assert m, (
        "orchestrator.sh 找不到 `if \"...done-mark...\" in logtxt:` 判斷行——"
        "validate 端 skip-only 偵測邏輯可能被改，contract 需同步檢視"
    )
    return m.group(1)


def test_cloud_embed_warning_contains_orchestrator_skip_token():
    """隱性 contract 鎖（IT-1，批5 Codex 留檔③『SKIP done-mark: 無測試鎖』）。

    cloud_embed 對全 skip / 有失敗批印的 warning 字串，必須**含** orchestrator
    validate（indexing_orchestrator.sh）grep 判準的確切 substring。兩側靠字面值
    隱性耦合：改任一側文案而未同步另一側 → orchestrator 偵測不到 skip-only 批
    → skip-only 批被誤判通過 validate → 記 VPS done-set → 該批文章永久漏 embed
    （IT-1 縫回歸）。

    本測試從『兩側真相源』各取字面值比對，任一側漂移即紅：
      - orchestrator 端：直接讀 .sh 抽 grep token（非硬編碼常數）。
      - cloud_embed 端：實際觸發 skip-only 批、捕捉真 warning 字串。
    """
    orch_token = _extract_orchestrator_skip_token()

    # cloud_embed 端：捕捉 skip-only 批真 warning（monkeypatch logger.warning，
    # ordering-免疫，同 test_main_skip_only_batch_logs_warning 手法）。
    warn_calls: list[str] = []
    real_warning = ce.logger.warning

    def _spy_warning(msg, *args, **kwargs):
        warn_calls.append(str(msg))
        return real_warning(msg, *args, **kwargs)

    with patch.object(ce.logger, "warning", _spy_warning):
        # _should_mark_done False（全 skip）→ main() 的 else 分支印 SKIP warning。
        # 直接跑純函式判準 + 端到端一條，確保捕捉到的是真 emit 的字串。
        with patch.object(ce, "get_model", lambda: None), \
             patch.object(ce, "process_tsv",
                          lambda *a, **k: {"success": 0, "failed": 0, "skipped": 3, "chunks": 0}):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                tsv_dir = Path(td) / "tsv"
                out_dir = Path(td) / "out"
                tsv_dir.mkdir()
                out_dir.mkdir()
                (tsv_dir / "allskip.tsv").write_text("dummy\n", encoding="utf-8")
                with patch.object(ce.sys, "argv",
                                  ["cloud_embed.py", str(tsv_dir), str(out_dir)]):
                    ce.main()

    skip_warnings = [m for m in warn_calls if orch_token in m]
    assert skip_warnings, (
        f"cloud_embed 的 skip-only warning 未含 orchestrator grep 判準 "
        f"{orch_token!r}（兩側 contract 漂移）。實際 warnings={warn_calls}"
    )
