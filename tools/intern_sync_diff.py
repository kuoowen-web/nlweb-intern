#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Intern repo 同步 delta 盤點（唯讀）。

自動化 docs/specs/intern-repo-sync-spec.md「第二鐵律」要求的逐檔內容比對：
commit message / 時間戳是敘事不是事實，算 delta 的唯一可信方法是
**逐檔內容比對**（兩邊 HEAD blob，CRLF-normalize 後 diff）。

本腳本只負責「發現」：clone（讀）+ 內容比對 + check-ignore + import 掃描。
push 與敏感掃描（spec Phase 3 / Phase 4）由人把關，不在腳本範圍。

嚴格唯讀：絕不 git add / commit / push、不修改任何檔案、不寫回 intern repo。
腳本內部的 git clone（唯讀，跑完清）是唯一允許的 git 寫盤操作。

**比對基準 = 兩邊 HEAD blob（committed 內容），不是 working tree。**（缺陷 1 修正）
  讀 working tree 會被「主 repo 未提交的編輯」「重用 clone 的本機改動」污染，
  導致同一 HEAD 兩次跑出不同數字（X<->Y 飄動）。改讀 `git show HEAD:<rel>` 後分類只依賴 HEAD。

輸出清單：
  (X) 內容一致              consistent
  (Y) 內容落後              behind          （兩邊 HEAD 都有但內容不同；報告再切兩區 ↓）
      ├ 普通技術檔：可直接複製
      └ root meta 雙向分歧：禁盲複製、需逐行 merge（.gitignore 等，缺陷 3）
  (intern_only) 主 HEAD 無、intern 有  待人工決定去留（附依賴證據，**不下「該刪」結論**，缺陷 2）
  (missing_in_intern) 主有、intern 缺  可能被 .gitignore 誤擋（第一鐵律）

另對每個落後檔印 intern 端 git check-ignore -v 狀態（第一鐵律：全域 glob 可能誤擋核心模組），
並掃 code/python 的 import core.* 比對主 repo 是否有對應檔（防連環缺檔）。

用法：
    python tools/intern_sync_diff.py                       # 自己 clone nlweb-intern(master) 到 temp
    python tools/intern_sync_diff.py --intern-path <既有clone路徑>   # 用既有 clone，不重 clone

退出碼：0 = 跑完（不論盤點結果）；非 0 = 腳本本身出錯（clone 失敗 / git 失敗等）。
"""
import argparse
import subprocess
import sys
import tempfile
import shutil
import re
import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INTERN_REPO_URL = "https://github.com/kuoowen-web/nlweb-intern.git"
INTERN_BRANCH = "master"

# intern repo 也包含的技術碼路徑前綴（與 .claude/scripts/intern-sync-reminder.py 的 TECH_PREFIXES 對齊）。
# 純 docs(非 specs)/memory/legal/status 不在此列。
TECH_PREFIXES = (
    "code/",
    "config/",
    "static/",
    "docs/specs/",
)
# 技術 spec 裡屬於敏感/內部、不該同步 intern 的（與 hook 的 EXCLUDE 對齊）。
EXCLUDE = (
    "docs/specs/intern-repo-sync-spec.md",  # 同步流程本身是內部運維文件
)

# 排除規則（glob / 路徑判斷）—— CEO 拍板：intern 只要核心技術碼，
# 不要前端二進位/視覺資產，也不要前端測試檔。
# 這些檔即便落在 TECH_PREFIXES（static/）下也一律排除在內容比對範圍之外，
# 它們不是「技術子集」的核心，徒增同步雜訊（170+ 缺檔多半是 static/images 圖檔）。
#   - static/images/* : SVG icon / png 等視覺二進位資產（非技術碼）。
#   - *.test.js       : 前端測試檔（路徑通常在 static/js/**/__tests__/），intern 練的是核心碼非測試。
# 保留（不在此列）：所有 .py、config/（去敏感值的技術設定）、docs/specs/、
#                  static/ 下的核心前端技術碼（非 images、非 test，如 static/js/foo.js、static/css/*）。
EXCLUDE_PATTERNS = (
    "static/images/*",  # 前端二進位/視覺資產：SVG icon 等，非核心技術碼
    "*.test.js",        # 前端測試檔（static/js/**/__tests__/*.test.js）
)

# 缺陷 3：root meta 檔「兩邊都有但內容不同」時，**絕不可盲複製主版覆蓋 intern 版**。
# 這些檔兩 repo 雙向分歧，且 intern 版有不可丟的東西，必須逐行 merge：
#   - .gitignore：intern 端有第一鐵律的 negation 救命規則
#       （`!...analyze_query.py`、`!...upload_rate_limit.py`），主 repo 沒有。
#       盲複製主版會刪掉 negation → 第一鐵律事故（核心模組被靜默 ignore）重演。
#   - .env.example / .dockerignore / Dockerfile / AGENTS.md：環境/meta 分歧，各有各的設定。
# 這些檔即使不在 TECH_PREFIXES、也非 .py，仍要納入比對（見 list_tracked_tech_files），
# 落 behind(Y) 桶時報告會單獨成區並標「禁盲複製、需逐行 merge」（見 run_report）。
MANUAL_MERGE_FILES = (
    ".gitignore",
    ".env.example",
    ".dockerignore",
    "Dockerfile",
    "AGENTS.md",
)
# 每個 manual-merge 檔的「為什麼不能盲複製」說明（報告用）。
MANUAL_MERGE_REASONS = {
    ".gitignore": "intern 版有第一鐵律 negation 救命規則"
                  "（!...analyze_query.py / !...upload_rate_limit.py），"
                  "主 repo 無 → 盲複製主版會刪掉 negation，第一鐵律事故重演",
    ".env.example": "環境變數樣板兩邊分歧（intern 可能已 strip 敏感預設值）",
    ".dockerignore": "build context 排除規則兩邊分歧",
    "Dockerfile": "建置/部署設定兩邊分歧",
    "AGENTS.md": "agent 操作 meta 兩邊分歧",
}


def is_manual_merge(rel: str) -> bool:
    """rel（repo 相對、forward-slash）是否為「禁盲複製、需逐行 merge」的 root meta 檔。"""
    return rel.replace("\\", "/") in MANUAL_MERGE_FILES


def is_excluded(rel: str) -> bool:
    """rel（repo 相對、forward-slash 路徑）是否命中任一排除規則。

    用 fnmatch 對「整路徑」與「basename」兩種形式比對：
      - "static/images/*" 對整路徑（含子目錄），命中 static/images/ 底下任意層級。
      - "*.test.js" 對 basename，命中任何目錄下的 *.test.js。
    """
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    for pat in EXCLUDE_PATTERNS:
        if "/" in pat:
            # 路徑型 pattern：對整路徑比，並涵蓋子目錄（static/images/a/b.svg）
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*"):
                return True
        else:
            # 檔名型 pattern：對 basename 比
            if fnmatch.fnmatch(name, pat):
                return True
    return False


class SyncDiffError(Exception):
    """腳本層級的硬失敗（不可 silent fail —— 明確報錯往上拋）。"""


# ----------------------------------------------------------------------
# 純函式：內容比對核心（被 TDD 測試驅動）
# ----------------------------------------------------------------------

def _normalize(data: bytes) -> bytes:
    """CRLF / 單獨 CR 一律正規化成 LF。Windows 環境 CRLF vs LF 不算內容差異。"""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _read_head_normalized(repo_root: Path, rel: str):
    """讀「該 repo HEAD commit 裡」rel 檔的 blob bytes，normalize 後回傳。
    檔不在 HEAD（未追蹤 / 已刪 / 不存在）回 None。

    缺陷 1 修正（Y 落後不可重現的根因）：
      原版用 path.read_bytes() 讀 **working tree**，但同步語意（spec 第二鐵律）
      明定算 delta 是「intern HEAD working tree vs 主 HEAD」——比的是 **committed 內容**。
      讀 working tree 會被「主 repo 未提交的編輯」「重用 clone 的本機改動」污染，
      導致同一 HEAD 兩次跑出不同 Y（X<->Y 飄動）。改讀 `git show HEAD:<rel>` 的 blob，
      分類只依賴 HEAD，與 working tree 髒污完全脫鉤 → 同一 HEAD 連跑必然一致。

    用 core.autocrlf=false 取「git 物件庫裡的原始 blob」（不受 checkout smudge 影響），
    再自己做 CRLF normalize，跨平台一致。
    """
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "show", f"HEAD:{rel}"],
        capture_output=True, cwd=str(repo_root),  # bytes 模式：不解碼，保住二進位/編碼原樣
    )
    if result.returncode != 0:
        # 檔不在 HEAD（git show 對缺檔回非 0）—— 視為「該 repo HEAD 無此檔」。
        return None
    return _normalize(result.stdout)


def classify_tech_files(main_root: Path, intern_root: Path, candidates):
    """對 candidates（repo 相對路徑字串）逐檔 **HEAD 內容** 比對，分成四類。

    基準是 HEAD（committed 內容），不是 working tree —— 見 _read_head_normalized
    的缺陷 1 說明。同一 HEAD 連跑結果必然一致。

    回傳 dict:
      consistent       -> [rel, ...]  兩邊 HEAD 都有且 normalize 後內容一致
      behind           -> [rel, ...]  兩邊 HEAD 都有但 normalize 後內容不同（真實漂移）
      intern_only      -> [rel, ...]  intern HEAD 有、主 HEAD 無（主無 intern 有，去留待人工決定）
                                      （原 key 名 deleted_in_main 暗示「已刪要刪」，缺陷 2 改中性命名）
      missing_in_intern-> [rel, ...]  主 HEAD 有、intern HEAD 無（intern 缺檔 / 可能被 .gitignore 誤擋）
    """
    consistent, behind, intern_only, missing_in_intern = [], [], [], []
    for rel in candidates:
        main_blob = _read_head_normalized(main_root, rel)
        intern_blob = _read_head_normalized(intern_root, rel)
        main_exists = main_blob is not None
        intern_exists = intern_blob is not None

        if main_exists and intern_exists:
            if main_blob == intern_blob:
                consistent.append(rel)
            else:
                behind.append(rel)
        elif intern_exists and not main_exists:
            intern_only.append(rel)
        elif main_exists and not intern_exists:
            missing_in_intern.append(rel)
        # 兩邊 HEAD 都沒有：candidate 來源理論上不會發生，略過
    return {
        "consistent": sorted(consistent),
        "behind": sorted(behind),
        "intern_only": sorted(intern_only),
        "missing_in_intern": sorted(missing_in_intern),
    }


# ----------------------------------------------------------------------
# git 互動（唯讀）
# ----------------------------------------------------------------------

def _run_git(args, cwd, *, check=True):
    """跑 git 指令。不可 silent fail：check=True 時非 0 退出碼直接拋 SyncDiffError。"""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
    )
    if check and result.returncode != 0:
        raise SyncDiffError(
            f"git {' '.join(args)} (cwd={cwd}) 失敗 (exit {result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def list_tracked_tech_files(repo_root: Path):
    """列出該 repo 所有「tracked 技術檔」（落在 TECH_PREFIXES、不在 EXCLUDE）。

    用 git ls-files（只看 tracked）—— 未追蹤檔不算入比對範圍。
    額外保證：所有 .py 都納入（題目要求「至少所有 .py」），即使不在 TECH_PREFIXES 下。
    額外納入 MANUAL_MERGE_FILES（root meta：.gitignore / Dockerfile 等）—— 缺陷 3：
      這些檔即使非 .py、非 TECH_PREFIXES，也必須比對，否則「雙向分歧禁盲複製」的警告無從觸發。
    最後套 EXCLUDE_PATTERNS（CEO 拍板：去前端二進位資產 / 前端測試）。
    """
    result = _run_git(["ls-files"], cwd=repo_root)
    files = []
    for line in result.stdout.splitlines():
        f = line.strip().replace("\\", "/")
        if not f:
            continue
        if f in EXCLUDE:
            continue
        if is_excluded(f):
            continue
        if f.startswith(TECH_PREFIXES) or f.endswith(".py") or is_manual_merge(f):
            files.append(f)
    return files


def check_ignore_status(intern_root: Path, rel_path: str):
    """回傳 intern 端 git check-ignore -v 的狀態描述（第一鐵律：全域 glob 誤擋同名核心模組）。

    有輸出 = 被某條 .gitignore 規則擋（印出規則）；exit 1 無輸出 = 沒被擋。
    """
    # check-ignore 命中回 0、未命中回 1、錯誤回 128 —— 0/1 都是正常結果，不可當失敗。
    result = subprocess.run(
        ["git", "check-ignore", "-v", rel_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(intern_root),
    )
    if result.returncode == 0 and result.stdout.strip():
        return f"IGNORED by rule: {result.stdout.strip()}"
    if result.returncode == 1:
        return "not ignored"
    # 128 之類真錯誤：不可 silent，回明確訊息
    return f"check-ignore error (exit {result.returncode}): {result.stderr.strip()}"


# ----------------------------------------------------------------------
# import 完整性掃描
# ----------------------------------------------------------------------

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(core(?:\.[\w]+)*)\s+import\b|import\s+(core(?:\.[\w]+)*))",
    re.MULTILINE,
)


def scan_import_integrity(main_root: Path):
    """掃 code/python 下所有 import core.* / from core.* import，
    比對目標模組在主 repo 是否存在，列出任何斷裂。

    回傳 [(source_file_rel, imported_module, reason), ...]（空 = 無斷裂）。
    一個缺檔常代表 .gitignore 規則誤擋一整類（第一鐵律），要全抓。
    """
    py_root = main_root / "code" / "python"
    breaks = []
    if not py_root.is_dir():
        # 不可 silent fail：明確標記掃描跳過原因
        breaks.append(("<scan>", "code/python", f"目錄不存在: {py_root}"))
        return breaks

    for py in py_root.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            breaks.append((str(py.relative_to(main_root)).replace("\\", "/"), "<read>", f"讀檔失敗: {e}"))
            continue
        for m in _IMPORT_RE.finditer(text):
            module = m.group(1) or m.group(2)
            if not module:
                continue
            if not _module_resolves(py_root, module):
                rel = str(py.relative_to(main_root)).replace("\\", "/")
                breaks.append((rel, module, "目標模組在主 repo 找不到對應檔"))
    return breaks


def _module_resolves(py_root: Path, module: str):
    """core.x.y 能解析成 py_root/core/x/y.py 或 py_root/core/x/y/__init__.py 即算存在。

    對中段 module（import core.x 後實際用 core.x.y）寬鬆處理：只要任一前綴路徑存在即可。
    """
    parts = module.split(".")
    base = py_root.joinpath(*parts)
    if (base.with_suffix(".py")).is_file():
        return True
    if (base / "__init__.py").is_file():
        return True
    if base.is_dir():
        return True
    # 寬鬆：核心 package 根存在就不算斷裂（避免把 `import core.x` 的中段誤報）
    if len(parts) >= 1 and (py_root / parts[0]).is_dir():
        # package 根存在，但具體子模組找不到 —— 仍視為斷裂（要被抓出來）
        return False
    return False


# ----------------------------------------------------------------------
# 缺陷 2：intern_only 檔的依賴證據（幫人判斷去留，腳本不下「該刪」結論）
# ----------------------------------------------------------------------

# 掃描依賴證據時看的文字檔副檔名（程式碼 + 設定 + 文件 + 無副檔名腳本）。
_DEP_SCAN_SUFFIXES = (
    ".py", ".js", ".ts", ".html", ".htm", ".css",
    ".md", ".txt", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini",
    ".json", ".dockerfile",
)
_DEP_SCAN_EXTRA_NAMES = ("Dockerfile",)  # 無副檔名但要掃的檔名


def _looks_like_intern_specific(rel: str) -> bool:
    """檔名/路徑是否「intern 專屬」（INTERN 字樣 / intern-only 目錄）。"""
    low = rel.lower()
    name = rel.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if "intern" in name:
        return True
    # 路徑段落含 intern（intern-only 目錄）
    for seg in rel.replace("\\", "/").split("/")[:-1]:
        if "intern" in seg.lower():
            return True
    return False


def intern_dependency_evidence(intern_root: Path, rel: str):
    """掃 intern repo，蒐集「rel 這個檔被誰依賴」的客觀證據，**只發現、不下結論**。

    缺陷 2：原本 intern_only(Z) 桶一律掛在「主已刪」名下，語意暗示「要刪」。
    但這桶裡常有 intern 命脈（requirements.txt 靠它跑、PNG 靠它顯示 UI、INTERN_SETUP.md 是專屬文件）。
    腳本不該替人判斷「該不該刪」（那是判斷題不是比對題），只附依賴證據讓人定。

    回傳 dict（中性事實，無任何「建議刪」措辭）：
      referenced_by   -> [refsrc, ...]  intern 端哪些檔 import / script src / 文字引用了它
      intern_specific -> bool           檔名含 INTERN 或落在 intern-only 目錄（專屬檔）
      orphan          -> bool           無人引用且非專屬（疑似一次性孤兒腳本；僅供參考，去留仍由人定）
    """
    rel = rel.replace("\\", "/")
    basename = rel.rsplit("/", 1)[-1]
    stem = basename[:-3] if basename.endswith(".py") else basename  # py 模組名（給 import 比對）

    referenced_by = []
    # 掃 intern 端所有文字檔，找對 basename / 模組名 / 相對路徑的引用。
    for p in intern_root.rglob("*"):
        if not p.is_file():
            continue
        # 跳過 .git 內部 與 被掃檔自己
        rp = p.relative_to(intern_root).as_posix()
        if rp.startswith(".git/") or rp == rel:
            continue
        if p.suffix.lower() not in _DEP_SCAN_SUFFIXES and p.name not in _DEP_SCAN_EXTRA_NAMES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = False
        # (a) 直接提到 basename（涵蓋 Dockerfile/README/setup 的 `pip install -r requirements.txt`、
        #     HTML 的 <script src="...foo.js">、相對路徑引用等）
        if basename in text:
            hit = True
        # (b) python import 該模組（from stem import / import stem / import pkg.stem）
        elif p.suffix.lower() == ".py" and stem and stem != basename:
            if re.search(rf"\b(?:from|import)\s+(?:[\w.]+\.)?{re.escape(stem)}\b", text):
                hit = True
        if hit:
            referenced_by.append(rp)

    referenced_by = sorted(set(referenced_by))
    intern_specific = _looks_like_intern_specific(rel)
    orphan = (not referenced_by) and (not intern_specific)
    return {
        "referenced_by": referenced_by,
        "intern_specific": intern_specific,
        "orphan": orphan,
    }


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

def clone_intern(dest: Path):
    """clone intern repo（指定分支）到 dest。失敗不可 silent —— 拋 SyncDiffError。"""
    print(f"[clone] {INTERN_REPO_URL} (branch={INTERN_BRANCH}) -> {dest}")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", INTERN_BRANCH, INTERN_REPO_URL, str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SyncDiffError(
            "clone nlweb-intern 失敗（網路 / 權限 / repo 不存在 / 分支錯）。"
            f"\n  url={INTERN_REPO_URL} branch={INTERN_BRANCH}"
            f"\n  exit={result.returncode}"
            f"\n  stderr={result.stderr.strip()}"
        )
    print("[clone] OK")


def run_report(main_root: Path, intern_root: Path):
    """跑完整盤點並印報告。回傳 (X數, Y數, intern_only數) 供結尾總結。

    分類基準是 HEAD（committed 內容），與 working tree 髒污脫鉤（缺陷 1）。
    """
    print("=" * 72)
    print("Intern Repo 同步 Delta 盤點（唯讀；發現用，push/敏感掃描由人把關）")
    print("  比對基準: 兩邊 HEAD blob（committed 內容），CRLF-normalize 後比")
    print("            —— 不讀 working tree，故主 repo 未提交編輯 / 重用 clone 髒動都不影響結果")
    print(f"  主 repo   : {main_root}")
    print(f"  intern    : {intern_root}")
    print(f"  技術檔範圍: {', '.join(TECH_PREFIXES)} + 所有 *.py + root meta {MANUAL_MERGE_FILES}")
    print(f"  排除: {', '.join(EXCLUDE)}; pattern {', '.join(EXCLUDE_PATTERNS)}")
    print("=" * 72)

    # 候選集 = 主 repo tracked 技術檔 ∪ intern tracked 技術檔
    main_files = set(list_tracked_tech_files(main_root))
    intern_files = set(list_tracked_tech_files(intern_root))
    candidates = sorted(main_files | intern_files)
    print(f"\n候選技術檔（主∪intern, tracked）: {len(candidates)} 個"
          f"（主 {len(main_files)} / intern {len(intern_files)}）")

    result = classify_tech_files(main_root, intern_root, candidates)
    X = result["consistent"]
    Y_all = result["behind"]
    intern_only = result["intern_only"]
    M = result["missing_in_intern"]

    # 缺陷 3：把 behind(Y) 切成「普通可直接複製」與「root meta 需逐行 merge（禁盲複製）」兩區。
    Y_plain = [f for f in Y_all if not is_manual_merge(f)]
    Y_merge = [f for f in Y_all if is_manual_merge(f)]

    # --- (X) 一致 ---
    print(f"\n--- (X) 內容一致 [consistent]: {len(X)} 個 ---")
    for f in X:
        print(f"  = {f}")

    # --- (Y) 普通落後（可直接複製）+ check-ignore ---
    print(f"\n--- (Y) 內容落後 [behind]（普通技術檔，可直接複製）: {len(Y_plain)} 個 ---")
    print("    （intern HEAD 版與主 HEAD 不同，CRLF-normalize 後仍不同 —— 真實內容漂移）")
    for f in Y_plain:
        status = check_ignore_status(intern_root, f)
        print(f"  ~ {f}")
        print(f"      intern check-ignore: {status}")

    # --- (Y-merge) root meta 雙向分歧：禁盲複製，需逐行 merge（缺陷 3）---
    print(f"\n--- (Y-merge) ⚠️  雙向分歧 root meta — 禁盲複製，需逐行 merge: {len(Y_merge)} 個 ---")
    print("    （兩 repo 內容不同，且 intern 版有不可丟的東西。**絕不可用主 repo 版盲複製覆蓋**）")
    if not Y_merge:
        print("    （無）")
    for f in Y_merge:
        reason = MANUAL_MERGE_REASONS.get(f, "兩 repo 內容分歧，需人工逐行 merge")
        status = check_ignore_status(intern_root, f)
        print(f"  ⚠️  {f}")
        print(f"      為什麼禁盲複製: {reason}")
        print(f"      處置: 逐行 merge（保留 intern 端既有規則/設定），不可整檔覆蓋")
        print(f"      intern check-ignore: {status}")

    # --- (intern_only) 主 HEAD 無、intern 有 —— 待人工決定去留（缺陷 2：中性，無「該刪」）---
    print(f"\n--- (intern_only) 主 repo 無此檔、intern 有 [待人工決定去留]: {len(intern_only)} 個 ---")
    print("    （腳本只發現 + 附依賴證據，**去留由人定**。注意：requirements.txt / UI 資產 / "
          "INTERN_SETUP.md 等是 intern 命脈，盲刪會讓 intern 跑不起來）")
    for f in intern_only:
        ev = intern_dependency_evidence(intern_root, f)
        # 依賴狀態用中性事實描述
        if ev["intern_specific"]:
            dep = "intern 專屬檔（檔名/路徑含 intern）"
        elif ev["referenced_by"]:
            dep = f"被 intern 端 {len(ev['referenced_by'])} 處引用 → 有依賴"
        elif ev["orphan"]:
            # 中性：只陳述「沒掃到引用」這個事實，不臆測它是腳本還是 manifest，
            # 也不暗示該刪（manifest 如 requirements-dev.txt 常靠人手動 `pip install -r` 用，無檔內引用屬正常）。
            dep = "intern 端未掃到任何引用（可能是手動使用的 manifest，或一次性腳本 —— 去留由人判斷）"
        else:
            dep = "依賴狀態未定"
        print(f"  · {f}")
        print(f"      主 repo 無此檔，intern 有，依賴狀態: {dep}")
        if ev["referenced_by"]:
            shown = ev["referenced_by"][:5]
            more = "" if len(ev["referenced_by"]) <= 5 else f" …(+{len(ev['referenced_by']) - 5})"
            print(f"      被引用於: {', '.join(shown)}{more}")

    # --- (額外) 主有、intern 缺 ---
    print(f"\n--- (額外) 主 repo 有、intern 缺檔 [missing_in_intern]: {len(M)} 個 ---")
    print("    （第一鐵律：可能被 intern .gitignore 全域 glob 誤擋同名核心模組）")
    for f in M:
        status = check_ignore_status(intern_root, f)
        print(f"  ! {f}")
        print(f"      intern check-ignore: {status}")

    # --- import 完整性掃描 ---
    print(f"\n--- import 完整性掃描（主 repo code/python 的 import core.*）---")
    breaks = scan_import_integrity(main_root)
    if not breaks:
        print("  OK: 無 import core.* 斷裂")
    else:
        print(f"  斷裂 {len(breaks)} 處：")
        for src, mod, reason in breaks:
            print(f"  x {src}  ->  {mod}  ({reason})")

    # --- 結尾總結 ---
    print("\n" + "=" * 72)
    print(f"總結: X 一致 {len(X)} / Y 落後 {len(Y_all)} "
          f"(普通可複製 {len(Y_plain)} / 需 merge 禁盲複製 {len(Y_merge)}) "
          f"/ intern-only 待人工 {len(intern_only)}"
          f"  (intern 缺檔 {len(M)} / import 斷裂 {len(breaks)})")
    print("下一步（人把關）: 走 docs/specs/intern-repo-sync-spec.md")
    print("  Y 普通檔可複製；Y-merge 逐行 merge 禁盲複製；intern-only 去留由人判斷（看依賴證據）。")
    print("  Phase 3 敏感掃描（GATE 3）+ Phase 4 push —— 腳本不碰，由 Zoe/人執行。")
    print("=" * 72)
    return len(X), len(Y_all), len(intern_only)


def main():
    parser = argparse.ArgumentParser(
        description="Intern repo 同步 delta 盤點（唯讀）。"
    )
    parser.add_argument(
        "--intern-path",
        help="既有的 nlweb-intern clone 路徑。給了就用它（不重 clone）。",
    )
    parser.add_argument(
        "--main-root",
        default=str(REPO_ROOT),
        help=f"主 repo 路徑（預設 {REPO_ROOT}）。",
    )
    args = parser.parse_args()

    main_root = Path(args.main_root).resolve()
    if not (main_root / ".git").exists():
        raise SyncDiffError(f"主 repo 路徑不是 git repo: {main_root}")

    tmp_clone = None
    try:
        if args.intern_path:
            intern_root = Path(args.intern_path).resolve()
            if not intern_root.is_dir():
                raise SyncDiffError(f"--intern-path 不存在或不是目錄: {intern_root}")
            if not (intern_root / ".git").exists():
                raise SyncDiffError(f"--intern-path 不是 git repo（找不到 .git）: {intern_root}")
            print(f"[intern] 使用既有 clone: {intern_root}")
        else:
            tmp_clone = Path(tempfile.mkdtemp(prefix="nlweb-intern-sync-"))
            clone_intern(tmp_clone)
            intern_root = tmp_clone

        run_report(main_root, intern_root)
        return 0
    finally:
        if tmp_clone is not None:
            shutil.rmtree(tmp_clone, ignore_errors=True)
            print(f"[cleanup] 移除臨時 clone: {tmp_clone}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SyncDiffError as e:
        # 不可 silent fail：明確報錯 + 非 0 退出
        print(f"\n[ERROR] {e}", file=sys.stderr)
        sys.exit(2)
