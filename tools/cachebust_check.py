"""cachebust_check.py — 前端 ES module cache-bust importer 一致性 gate

背景：同一個 .js 被多處 import 時，若各 importer 的 `?v=` 版本 specifier 不一致，
瀏覽器以「解析後絕對 URL（含 query string）」當 module identity → 載入多份獨立
module instance。若該檔有 module-scope state（module-level let/const），state 會分裂
（一份寫、另一份讀 → 永遠 null）。這條坑反覆踩：lessons-frontend「cache-bust 連踩
五輪」+ 2026-07-15 AR R2 又踩（deep-research.js 4 個 importer 只 bump 1 個 →
_currentResearchQueryId state split，KG rerun 斷）。A1.5 工具化 gate 判準（≥2 次 +
機械可驗 + 可 script enforce）全中 → 產此工具，取代「改 JS 記得 sweep 全 importer」的
文字防線。

做什麼：掃 static/ 底下所有 .js/.html，找出每個被 import 的目標 .js（帶 `?v=` 的
import 語句 / <script src>），把「同一目標檔的所有 importer 版本」聚合，任何目標檔
出現 ≥2 種不同版本 → 報 module 分裂風險（FAIL）。

用法：
    python tools/cachebust_check.py            # 掃全 repo，有分裂則 exit 1
    python tools/cachebust_check.py --quiet    # 只印 FAIL/PASS 摘要

只讀不改。列出的分裂由人工把所有 importer bump 到同一版本（見 lessons-frontend
「同一檔所有 importer 的 specifier 必須字字相同」）。
"""
import re
import sys
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"

# 匹配 import ... from '....js?v=XXX'  或  <script src="....js?v=XXX">
# 抓 (目標檔基名, 版本字串)。目標檔可能含相對路徑，取 basename 聚合。
_IMPORT_RE = re.compile(r"""['"(]([^'"()\s]+?\.js)\?v=([A-Za-z0-9._-]+)['"()]""")


def scan():
    # target_basename -> { version -> [ (importer_relpath, target_specifier) ] }
    targets = defaultdict(lambda: defaultdict(list))
    if not STATIC.is_dir():
        print(f"[cachebust] static/ 不存在：{STATIC}")
        return targets
    for f in list(STATIC.rglob("*.js")) + list(STATIC.rglob("*.html")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            # 跳過 JS 行註解（`//` 開頭，含縮排）— 註解裡的 `?v=...` 說明文字非真 importer
            if line.lstrip().startswith("//"):
                continue
            for m in _IMPORT_RE.finditer(line):
                spec, ver = m.group(1), m.group(2)
                # 排除非字面版本（動態拼接 `?v=${...}` / 佔位 `?v=...`）
                if ver in ("...",) or "$" in ver:
                    continue
                base = spec.rsplit("/", 1)[-1]  # basename
                rel = f.relative_to(REPO).as_posix()
                targets[base][ver].append((rel, spec))
    return targets


def main():
    quiet = "--quiet" in sys.argv
    targets = scan()
    split = {b: vers for b, vers in targets.items() if len(vers) > 1}
    if not split:
        n = sum(len(v) for v in targets.values())
        print(f"[cachebust] PASS — {len(targets)} 個被 import 的 .js，版本全一致（掃 {n} 個 importer 引用）")
        return 0
    print(f"[cachebust] FAIL — {len(split)} 個目標檔的 importer 版本不一致（module 分裂風險）：\n")
    for base, vers in sorted(split.items()):
        print(f"  ● {base} — {len(vers)} 種版本：")
        for ver, importers in sorted(vers.items()):
            for rel, spec in importers:
                print(f"      ?v={ver:<14} ← {rel}  ({spec})")
        print(f"    → 修法：把上列所有 importer 的 {base} bump 到同一版本"
              f"（見 memory/lessons-frontend.md「同一檔所有 importer specifier 必須字字相同」）\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
