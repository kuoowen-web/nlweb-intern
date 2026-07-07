#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TDD 測試：intern_sync_diff.py 的三個缺陷修正。

全部用臨時 git repo（不連網、不碰真 intern repo）：
  缺陷 1：Y 落後不可重現 —— 分類必須基於 HEAD 內容，不受 working tree 髒污影響。
  缺陷 2：Z「主已刪」語意 —— 改成中性「主無 intern 有（待人工去留）」+ 附依賴證據，禁出現「該刪/建議刪」。
  缺陷 3：root meta 雙向分歧 —— .gitignore 等 5 檔落 Y 桶時額外標 manual-merge，分開列。

跑法：
    cd code/python && python -m pytest ../../tools/test_intern_sync_diff.py -q
  或直接：
    python tools/test_intern_sync_diff.py
"""
import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intern_sync_diff as m  # noqa: E402


def _git(args, cwd):
    """跑 git，失敗就拋（測試環境不可 silent）。"""
    r = subprocess.run(
        ["git"] + args, cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr or r.stdout}")
    return r


def _init_repo(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t.t"], root)
    _git(["config", "user.name", "t"], root)
    _git(["config", "commit.gpgsign", "false"], root)


def _write(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="\n")


def _commit_all(root: Path, msg="c"):
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", msg], root)


class TempRepos:
    """建一對 main / intern 臨時 git repo，測完清。"""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="isd-test-"))
        self.main = self.base / "main"
        self.intern = self.base / "intern"
        _init_repo(self.main)
        _init_repo(self.intern)

    def cleanup(self):
        shutil.rmtree(self.base, ignore_errors=True)


class TestYReproducible(unittest.TestCase):
    """缺陷 1：分類基於 HEAD，working tree 髒污不得改變 Y。"""

    def setUp(self):
        self.r = TempRepos()

    def tearDown(self):
        self.r.cleanup()

    def test_dirty_main_working_tree_does_not_flip_to_behind(self):
        # 兩邊 commit 相同內容 -> 應該 consistent
        _write(self.r.main, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.main)
        _write(self.r.intern, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.intern)

        # 主 repo working tree 髒污：改檔但「不 commit」（HEAD 仍是 x=1）
        _write(self.r.main, "code/python/foo.py", "x = 1\n# uncommitted edit\n")
        # 確認 HEAD 仍乾淨內容
        st = _git(["status", "--porcelain"], self.r.main).stdout
        self.assertIn("foo.py", st, "前置：working tree 應已被改髒")

        res = m.classify_tech_files(self.r.main, self.r.intern,
                                    ["code/python/foo.py"])
        self.assertIn("code/python/foo.py", res["consistent"],
                      "HEAD 內容一致時，主 working tree 的未提交編輯不該把檔判成 behind")
        self.assertNotIn("code/python/foo.py", res["behind"])

    def test_dirty_intern_clone_does_not_flip(self):
        # intern 端被重用 clone 改髒，也不該影響（基於 HEAD）
        _write(self.r.main, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.main)
        _write(self.r.intern, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.intern)
        _write(self.r.intern, "code/python/foo.py", "x = 999\n# dirty reused clone\n")

        res = m.classify_tech_files(self.r.main, self.r.intern,
                                    ["code/python/foo.py"])
        self.assertIn("code/python/foo.py", res["consistent"])

    def test_genuine_head_difference_still_behind(self):
        # 真實 HEAD 內容不同 -> 仍要判 behind（不能因為改用 HEAD 就漏報真漂移）
        _write(self.r.main, "code/python/foo.py", "x = 2\n")
        _commit_all(self.r.main)
        _write(self.r.intern, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.intern)
        res = m.classify_tech_files(self.r.main, self.r.intern,
                                    ["code/python/foo.py"])
        self.assertIn("code/python/foo.py", res["behind"])

    def test_crlf_only_difference_in_head_is_consistent(self):
        # HEAD blob 一個 CRLF 一個 LF，normalize 後一致 -> consistent
        (self.r.main / "code/python").mkdir(parents=True, exist_ok=True)
        (self.r.main / "code/python/foo.py").write_bytes(b"x = 1\r\ny = 2\r\n")
        _commit_all(self.r.main)
        (self.r.intern / "code/python").mkdir(parents=True, exist_ok=True)
        (self.r.intern / "code/python/foo.py").write_bytes(b"x = 1\ny = 2\n")
        _commit_all(self.r.intern)
        res = m.classify_tech_files(self.r.main, self.r.intern,
                                    ["code/python/foo.py"])
        self.assertIn("code/python/foo.py", res["consistent"])


class TestZSemantics(unittest.TestCase):
    """缺陷 2：Z 桶中性化 + 依賴證據，禁「該刪」措辭。"""

    def setUp(self):
        self.r = TempRepos()

    def tearDown(self):
        self.r.cleanup()

    def test_bucket_key_is_neutral_not_deleted(self):
        # 新 key 名不應再用「deleted_in_main」這種暗示「已刪要刪」的語意
        _write(self.r.main, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.main)
        _write(self.r.intern, "code/python/foo.py", "x = 1\n")
        _write(self.r.intern, "code/python/requirements.txt", "flask\n")
        _commit_all(self.r.intern)
        res = m.classify_tech_files(self.r.main, self.r.intern,
                                    ["code/python/foo.py",
                                     "code/python/requirements.txt"])
        # 新桶名（intern 有、主無、待人工）
        self.assertIn("intern_only", res,
                      "Z 桶應改名為中性 'intern_only'（主無 intern 有，待人工去留）")
        self.assertIn("code/python/requirements.txt", res["intern_only"])

    def test_dependency_evidence_marks_referenced_file(self):
        # requirements.txt 被 Dockerfile / setup 引用 -> 證據應標「被引用 / depended」
        _write(self.r.intern, "code/python/requirements.txt", "flask\n")
        _write(self.r.intern, "Dockerfile", "RUN pip install -r code/python/requirements.txt\n")
        _write(self.r.intern, "INTERN_SETUP.md", "pip install -r requirements.txt\n")
        _commit_all(self.r.intern)
        ev = m.intern_dependency_evidence(self.r.intern, "code/python/requirements.txt")
        # 證據結構：被引用清單非空
        self.assertTrue(ev["referenced_by"],
                        "被 Dockerfile/SETUP 引用的檔，referenced_by 應非空")
        self.assertFalse(ev["orphan"], "被引用的檔不是孤兒")

    def test_dependency_evidence_marks_orphan(self):
        # 沒人引用的一次性腳本 -> orphan
        _write(self.r.intern, "scripts/migrate_user_data_index.py", "print('migrate')\n")
        _commit_all(self.r.intern)
        ev = m.intern_dependency_evidence(self.r.intern, "scripts/migrate_user_data_index.py")
        self.assertEqual(ev["referenced_by"], [])
        self.assertTrue(ev["orphan"], "沒人引用的腳本應標 orphan")

    def test_dependency_evidence_marks_intern_specific(self):
        # 檔名含 INTERN -> intern 專屬
        _write(self.r.intern, "INTERN_SETUP.md", "intern only\n")
        _commit_all(self.r.intern)
        ev = m.intern_dependency_evidence(self.r.intern, "INTERN_SETUP.md")
        self.assertTrue(ev["intern_specific"], "檔名含 INTERN 應標 intern_specific")

    def test_dependency_evidence_detects_python_import(self):
        # intern 端某 py import 了這個模組 -> referenced_by 非空、非孤兒
        _write(self.r.intern, "code/python/helper.py", "def h(): pass\n")
        _write(self.r.intern, "code/python/app.py", "from helper import h\n")
        _commit_all(self.r.intern)
        ev = m.intern_dependency_evidence(self.r.intern, "code/python/helper.py")
        self.assertTrue(ev["referenced_by"], "被 import 的模組 referenced_by 應非空")
        self.assertFalse(ev["orphan"])

    def test_report_text_has_no_delete_recommendation(self):
        # 整份報告輸出不得出現「該刪 / 建議刪 / 要刪」
        _write(self.r.main, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.main)
        _write(self.r.intern, "code/python/foo.py", "x = 1\n")
        _write(self.r.intern, "code/python/requirements.txt", "flask\n")
        _write(self.r.intern, "Dockerfile", "RUN pip install -r code/python/requirements.txt\n")
        _commit_all(self.r.intern)

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.run_report(self.r.main, self.r.intern)
        out = buf.getvalue()
        for forbidden in ["建議刪", "該刪", "要刪", "請刪", "刪除這"]:
            self.assertNotIn(forbidden, out,
                             f"Z/intern_only 區報告不得出現誘導刪除的措辭：{forbidden!r}")


class TestRootMetaManualMerge(unittest.TestCase):
    """缺陷 3：root meta 雙向分歧，落 Y 桶要額外標 manual-merge、分開列。"""

    def setUp(self):
        self.r = TempRepos()

    def tearDown(self):
        self.r.cleanup()

    def test_gitignore_diff_flagged_manual_merge(self):
        # .gitignore 兩邊不同 -> 應進 manual-merge 桶（不是普通 behind）
        _write(self.r.main, ".gitignore", "analyze_*.py\n!core/retry_util.py\n")
        _commit_all(self.r.main)
        _write(self.r.intern,
               ".gitignore",
               "analyze_*.py\n!code/python/core/query_analysis/analyze_query.py\n")
        _commit_all(self.r.intern)

        res = m.classify_tech_files(self.r.main, self.r.intern, [".gitignore"])
        # .gitignore 兩邊都有但內容不同 -> behind，且應被標 manual-merge
        self.assertIn(".gitignore", res["behind"])
        self.assertTrue(m.is_manual_merge(".gitignore"),
                        ".gitignore 應被識別為 manual-merge 檔")

    def test_normal_py_not_manual_merge(self):
        self.assertFalse(m.is_manual_merge("code/python/foo.py"))

    def test_all_five_root_meta_recognized(self):
        for f in [".gitignore", ".env.example", ".dockerignore", "Dockerfile", "AGENTS.md"]:
            self.assertTrue(m.is_manual_merge(f), f"{f} 應在 manual-merge 名單")

    def test_root_meta_files_are_candidates(self):
        # root meta 檔即使不在 TECH_PREFIXES / 非 .py，也要被納入候選比對
        _write(self.r.main, ".gitignore", "a\n")
        _write(self.r.main, "code/python/foo.py", "x\n")
        _commit_all(self.r.main)
        files = m.list_tracked_tech_files(self.r.main)
        self.assertIn(".gitignore", files,
                      "root meta 檔（.gitignore 等）應被列為候選技術檔以便比對")

    def test_report_separates_manual_merge_from_plain_behind(self):
        # 報告把 manual-merge 檔單獨成區，且帶警告字樣
        _write(self.r.main, ".gitignore", "main-version\n")
        _write(self.r.main, "code/python/foo.py", "x = 2\n")
        _commit_all(self.r.main)
        _write(self.r.intern, ".gitignore", "intern-version\n!analyze_query.py\n")
        _write(self.r.intern, "code/python/foo.py", "x = 1\n")
        _commit_all(self.r.intern)

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m.run_report(self.r.main, self.r.intern)
        out = buf.getvalue()
        self.assertIn("禁盲複製", out, "manual-merge 區應有『禁盲複製』警告")
        self.assertIn(".gitignore", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
