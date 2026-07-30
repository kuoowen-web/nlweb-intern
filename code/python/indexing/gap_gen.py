"""
Dynamic Gap Generator — weekly indexing 每輪起跑時**現算**本輪缺口 TSV 清單。

Plan B v8.1 G9（`docs/in progress/plans/indexing-orchestrator-vm-plan.md` §G9）的落地。
取代 backfill 期凍結的 `gap_full.txt`：weekly 無人值守下，凍結清單會「重做已做 or 漏做
新增」，兩頭都是錢/資料損失。本模組每輪 diff 出真缺口。

觸發源＝`docs/crawling-lessons-0712.md` 教訓 #3（舊清單算法算錯 547 檔）：
  bug (a) source 名不同構——crawler 檔名短名（`chinatimes`）vs VPS `articles.source` 全名
       （`chinatimes.com`，＝ urlparse(url).netloc.replace("www.","")）直接比對必 miss。
  bug (b) 粒度錯——crawler 一天多檔（`_HH-MM` 批次戳）vs VPS 按 (source,date) 聚合，
       拿檔名比聚合表 → 永遠不匹配 → 全算缺口。
本模組把「雙向抽驗」真工具化為 **URL 級**（見下方設計），(source,date) 聚合比對只作
advisory log（教訓 #3 連帶記載：92 個「污染」親查只 38——批次日 ≠ 發布日，日期語義比對
每個週界必產跨桶誤差，fail-closed 會誤殺正常輪）。

═══════════════════════════════════════════════════════════════════════════
兩層設計
═══════════════════════════════════════════════════════════════════════════
1. 主差集＝檔名級：
     gap = crawler TSV 檔名集 − VPS done-set − skip-set
   done-set 穩態下權威（backfill 已把歷史對齊），檔名 diff 精確、便宜、與工作單位
   （TSV 檔）同構。

   ⚠ TSV 不可變假設（G9 前提，crawler 改行為時必須回來動這裡）：
     crawler 檔名帶 `_YYYY-MM-DD_HH-MM` 批次戳、一次寫成不改寫（見
     crawler/core/pipeline.py `_init_output_path`：`{source}_{時間戳}.tsv`）。
     若未來 crawler 改為覆寫同名檔，檔名 diff 即失效——同名檔內容變了但檔名沒變，
     done-set 會誤判已做。

2. mtime 排除：mtime < GAP_CONFIG.mtime_exclude_minutes（初值 60）分鐘的 TSV 不進
   本輪清單（防抓到 crawler 寫入中半檔）。清單為空＝正常退出（exit 0），本週無新
   工作是合法狀態，非 abort。

3. 抽驗層＝URL 級雙向抽驗（教訓 #3 的真工具化，**不是 (source,date) 比對**）：
   每輪抽 N 檔（config，初值各 3），抽樣偏向 URL 數 ≥ min-N（config 初值 20）的檔
   （防退化週全小檔時零強制判準）；單檔 URL 數 < min-N 只記 advisory 不納判準
   （比例判準對 3-8 URL 小檔一筆 re-crawl/濾除即誤 abort）：
     - 「判缺口」檔（在 gap 內）：從 TSV 抽 URL 查 VPS，命中率應 ≤ 上水位（初值 10%）。
     - 「判已有」檔（在 done-set 內、且 crawler 現存）：命中率應 ≥ 下水位（初值 80%）。
   任一越界 → 本輪 abort + CRITICAL 告警（附逐檔命中明細供 triage）。
   判準用水位不用絕對值：done 檔的 raw TSV URL 不會 100% 入庫（bulk_load quality gate
   會濾 body<50 字 / 中文<20%）；gap 檔也可能少量命中（罕見 re-crawl）。

4. source 名顯式映射表（bug (a) 防線）：crawler 短名 ↔ VPS `articles.source` 全名走
   映射表；查無映射的新 source → fail-loud abort + 告警（new-source onboarding 防線，
   不靜默）。(source,date) 聚合比對降級為 advisory log（只報不擋）。

═══════════════════════════════════════════════════════════════════════════
fail-closed 契約
═══════════════════════════════════════════════════════════════════════════
gap_gen 任何 fail（抽驗越界 / 映射缺 / VPS 不通 / 讀 crawler 清單失敗）
  → 非零退出 + CRITICAL 告警，**絕不 fallback 讀舊 gap_full.txt**。
launcher 端據非零退出 abort（stale 清單＝重做已做 or 漏做新增）。

═══════════════════════════════════════════════════════════════════════════
介面契約（給 G11 executor 對接）
═══════════════════════════════════════════════════════════════════════════
- gap_gen **讀** skip-set 檔 `/data/indexed_skipped.txt`（G11 寫）；
  格式＝一行一 TSV 檔名。本模組定義讀取 helper（`load_skip_set`）；G11 executor
  實作寫入端。
- gap_gen **不寫** failures/skipped（那是 G11 orchestrator/launcher 的事）。
- gap_gen **只寫** per-run gap 清單（launcher 傳 `--out gap_<launcher_run_id>.txt`，
  tmp→mv 原子寫）。

═══════════════════════════════════════════════════════════════════════════
可測性（依賴注入）
═══════════════════════════════════════════════════════════════════════════
所有「連機器」的操作都抽成可注入函式，unit test 不碰網路：
- list_crawler_tsvs：列 crawler VM `~/nlweb/data/crawler/articles/*.tsv`（+ mtime）。
- load_done_set：讀 VPS `/data/indexed_tsvs.txt`。
- load_skip_set：讀 VPS `/data/indexed_skipped.txt`（G11 契約）。
- vps_url_hits：查一批 article URL 在 VPS 的命中集合（走 indexer_import 唯讀 SELECT）。
- read_tsv_urls：讀單一 crawler TSV 的 article URL 欄（col 0）。
production 端由 launcher 用 SSH/tunnel 實作；本模組只吃「純資料」介面。
"""
from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] gap_gen: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Config（G9：水位 / N / min-N 全放 config，Phase D 校準）
# ═══════════════════════════════════════════════════════════════════════════

# source 短名（crawler 檔名）↔ VPS articles.source 全名（＝ urlparse(url).netloc
# .replace("www.","")）。查無映射的新 source → fail-loud（見 SourceMappingError）。
#
# 值來源：crawler/parsers/*_parser.py 的 source_name() + 各 source 真實 URL domain
# （親查 data/crawler/articles/*.tsv col0 確認）。cloud_embed.py 只 strip "www."，
# 故 ltn 保留 "news." 前綴。
#   ⚠ ltn/cna 等多子域 source 的 VPS 全名以 Phase D per-source 命中分佈校準為準
#   （本表 seed 自 URL domain，若 VPS articles.source 實際存法不同須回來對齊——
#   但這只影響 (source,date) advisory log，不影響主差集/URL 抽驗，見模組 docstring）。
DEFAULT_SOURCE_MAP: dict[str, str] = {
    "chinatimes": "chinatimes.com",
    "cna": "cna.com.tw",
    "ltn": "news.ltn.com.tw",
    "udn": "udn.com",
    "moea": "moea.gov.tw",
    "einfo": "e-info.org.tw",
    "esg_businesstoday": "esg.businesstoday.com.tw",
}


@dataclass
class GapConfig:
    """抽驗 / 排除參數（G9：全放 config，Phase D 校準）。"""

    # mtime < 這麼多分鐘的 TSV 不進本輪清單（防抓 crawler 寫入中半檔）
    mtime_exclude_minutes: int = 60
    # 每輪各抽幾檔做 gap / done 抽驗
    sample_gap_n: int = 3
    sample_done_n: int = 3
    # 單檔 URL 數 ≥ 這值才納入判準（否則只記 advisory）；抽樣偏向這類檔
    min_urls_for_verdict: int = 20
    # 「判缺口」檔命中率上水位：命中率 > 這值 → 越界 abort（該檔其實已有）
    gap_hit_ceiling: float = 0.10
    # 「判已有」檔命中率下水位：命中率 < 這值 → 越界 abort（該檔其實缺）
    done_hit_floor: float = 0.80
    # 每檔查 VPS 命中時，最多抽幾個 URL（省 SELECT；None＝全查）
    urls_per_file_probe: Optional[int] = 200
    # 抽樣隨機種子（None＝真隨機；test 可固定）
    seed: Optional[int] = None
    # source 短名 → VPS 全名映射
    source_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SOURCE_MAP))


# ═══════════════════════════════════════════════════════════════════════════
# 例外（fail-closed 契約：全部非零退出 + CRITICAL 告警）
# ═══════════════════════════════════════════════════════════════════════════


class GapGenError(Exception):
    """gap_gen 致命錯誤基類。任何 raise → 非零退出、絕不 fallback 舊清單。"""


class SourceMappingError(GapGenError):
    """crawler 檔名帶未知 source 短名（查無映射）→ new-source onboarding 防線。"""


class SamplingVerdictError(GapGenError):
    """URL 級抽驗越界（gap 檔命中太多 / done 檔命中太少）。附逐檔明細。"""


class CrawlerListError(GapGenError):
    """列 crawler TSV 清單失敗（SSH 不通等）。"""


class VpsQueryError(GapGenError):
    """查 VPS URL 命中失敗（tunnel / DB 不通）。"""


# ═══════════════════════════════════════════════════════════════════════════
# 純資料型別
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TsvEntry:
    """一個 crawler TSV 檔的最小描述（可注入，test 餵假清單）。"""

    name: str          # 檔名，如 "chinatimes_2026-07-15_03-12.tsv"
    mtime_epoch: float  # 修改時間（epoch 秒）；用於 mtime 排除


@dataclass
class SampleResult:
    """單檔 URL 抽驗結果。"""

    name: str
    kind: str          # "gap" | "done"
    total_urls: int    # 該檔 TSV 的 article URL 總數
    probed: int        # 實際查 VPS 的 URL 數
    hits: int          # 命中數
    hit_rate: float    # hits / probed（probed==0 時為 0.0）
    counted: bool      # 是否納入判準（total_urls >= min_urls_for_verdict）
    breached: bool     # 是否越界（僅 counted 檔有意義）

    def line(self) -> str:
        tag = "COUNTED" if self.counted else "advisory(小檔)"
        flag = " ⚠BREACH" if self.breached else ""
        return (
            f"  [{self.kind}] {self.name}: {self.hits}/{self.probed} 命中"
            f"（率 {self.hit_rate:.0%}，URL 總 {self.total_urls}，{tag}）{flag}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 檔名解析（bug (a) 防線的入口）
# ═══════════════════════════════════════════════════════════════════════════


def parse_source_shortname(tsv_name: str) -> str:
    """從 crawler TSV 檔名抽 source 短名。

    檔名格式（crawler/core/pipeline.py `_init_output_path`）：
      {source}_{YYYY-MM-DD_HH-MM}.tsv          # 一般
      {source}_{YYYY-MM}.tsv                    # chunk_by_month
      {source}_..._part000.tsv                  # chunk_size 分檔
    source 短名可能含底線（`esg_businesstoday`），所以不能單純 split('_')[0]。
    策略：切掉 .tsv → 從右邊剝掉「純數字/日期/part 片段」直到剩 source。

    以第一段「像日期或 part 的 token」為切點——source 短名不含這種 token。
    """
    stem = tsv_name
    if stem.endswith(".tsv"):
        stem = stem[: -len(".tsv")]
    tokens = stem.split("_")
    src_tokens: list[str] = []
    for tok in tokens:
        if _looks_like_stamp_token(tok):
            break
        src_tokens.append(tok)
    if not src_tokens:
        # 整個檔名都像 stamp（不該發生）——當作無法解析，交映射查找 fail-loud
        return stem
    return "_".join(src_tokens)


def _looks_like_stamp_token(tok: str) -> bool:
    """判斷一個底線分段是否為「日期/時間/part 批次戳片段」而非 source 名片段。

    stamp 片段：純數字（年月日時分）、YYYY-MM-DD / HH-MM（含連字號的數字組）、
    partNNN。source 短名（chinatimes / esg / businesstoday / einfo ...）都含字母
    且不是 partNNN。
    """
    if not tok:
        return False
    if tok.startswith("part") and tok[4:].isdigit():
        return True
    # 去掉連字號後全數字 → 日期/時間片段（"2026-07-15" / "03-12" / "2026"）
    if tok.replace("-", "").isdigit() and any(c.isdigit() for c in tok):
        return True
    return False


def map_source(shortname: str, source_map: dict[str, str]) -> str:
    """短名 → VPS 全名。查無 → fail-loud（new-source onboarding 防線）。"""
    if shortname not in source_map:
        raise SourceMappingError(
            f"未知 source 短名 '{shortname}'（查無映射）——新 source 上線需先補進 "
            f"GAP_CONFIG.source_map。已知：{sorted(source_map)}。"
            f"（教訓 #3 bug (a)：短名/全名 mismatch 防線，不靜默）"
        )
    return source_map[shortname]


# ═══════════════════════════════════════════════════════════════════════════
# skip-set / done-set helper（可注入；預設走檔案）
# ═══════════════════════════════════════════════════════════════════════════


def _read_name_set(text: str) -> set[str]:
    """一行一檔名的文字 → set（strip、去 \\r、去空行）。教訓 #7 \\r 防線。"""
    out: set[str] = set()
    for line in text.splitlines():
        t = line.strip().replace("\r", "")
        if t:
            out.add(t)
    return out


def load_skip_set(path: str = "/data/indexed_skipped.txt") -> set[str]:
    """讀 G11 的正式 skip 檔（一行一 TSV 檔名）。

    介面契約（給 G11）：gap_gen 只讀不寫此檔；缺檔＝空 skip-set（首次上線正常態）。
    launcher/orchestrator 用同款解析（共用此 helper 或等效邏輯）。
    """
    p = Path(path)
    if not p.exists():
        return set()
    return _read_name_set(p.read_text(encoding="utf-8", errors="ignore"))


def load_done_set(path: str = "/data/indexed_tsvs.txt") -> set[str]:
    """讀 VPS done-set（一行一 TSV 檔名）。缺檔＝空（fail-loud 交呼叫端判斷是否合理）。"""
    p = Path(path)
    if not p.exists():
        return set()
    return _read_name_set(p.read_text(encoding="utf-8", errors="ignore"))


def read_tsv_urls(tsv_path: str) -> list[str]:
    """讀單一 crawler TSV 的 article URL 欄（col 0，tab 分隔第一欄）。

    TSV 格式（cloud_embed.py `parse_tsv_line`）：`url<TAB>json_ld_str`，split('\\t',1)。
    URL 欄 schema 假設 fail-loud：若某行無 tab 或 col0 非 http(s) URL → 記警告跳過該行
    （單行髒不 abort，但整檔零合法 URL 由呼叫端當 advisory 處理）。
    """
    urls: list[str] = []
    with open(tsv_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            url = parts[0].strip()
            if not url:
                continue
            scheme = urlparse(url).scheme
            if scheme not in ("http", "https"):
                # URL 欄 schema 破壞——記警告（不 silent），跳過該行
                logger.warning("TSV %s 有非 URL 開頭的行（scheme=%r），跳過", Path(tsv_path).name, scheme)
                continue
            urls.append(url)
    return urls


# ═══════════════════════════════════════════════════════════════════════════
# 主差集（檔名級）+ mtime 排除
# ═══════════════════════════════════════════════════════════════════════════


def _tsv_only(name: str) -> bool:
    """只收 .tsv，排除 .tsv.pg_checkpoint.json 等 sidecar。"""
    return name.endswith(".tsv")


def compute_gap(
    crawler_tsvs: Iterable[TsvEntry],
    done_set: set[str],
    skip_set: set[str],
    now_epoch: float,
    config: GapConfig,
) -> list[str]:
    """主差集 + mtime 排除。回傳排序後的 gap TSV 檔名清單。

    gap = crawler TSV 檔名集 − done-set − skip-set，再排除 mtime 太新的檔。
    """
    cutoff = config.mtime_exclude_minutes * 60
    gap: set[str] = set()
    for e in crawler_tsvs:
        if not _tsv_only(e.name):
            continue
        if e.name in done_set or e.name in skip_set:
            continue
        age = now_epoch - e.mtime_epoch
        if age < cutoff:
            logger.info(
                "mtime 排除：%s（age %.0f 分 < %d 分，疑寫入中半檔）",
                e.name, age / 60, config.mtime_exclude_minutes,
            )
            continue
        gap.add(e.name)
    return sorted(gap)


# ═══════════════════════════════════════════════════════════════════════════
# URL 級雙向抽驗
# ═══════════════════════════════════════════════════════════════════════════


def _pick_sample(
    candidates: list[str],
    n: int,
    size_of: Callable[[str], int],
    min_urls: int,
    rng: random.Random,
) -> list[str]:
    """抽 n 檔，偏向 size（URL/行數）≥ min_urls 的檔（G9 抽樣偏向）。

    先從「大檔池」抽；不足才從「小檔池」補（退化週全小檔時仍抽得到，只是判準降 advisory）。

    ⚠ `size_of` 是**純本地 dict 查詢**（run_sampling 已把候選全集的 size 一次批次撈進
      dict，見下方 `tsv_sizes_of` 契約）——這裡對候選全集呼叫它是零 I/O 成本。真正的
      URL 級判準（counted）在 `_probe_file` 用實拉檔的 `read_tsv_urls` 算，size_of 只影響
      「抽哪 N 檔」的偏向。
    """
    if not candidates:
        return []
    big = [c for c in candidates if size_of(c) >= min_urls]
    small = [c for c in candidates if size_of(c) < min_urls]
    rng.shuffle(big)
    rng.shuffle(small)
    picked = big[:n]
    if len(picked) < n:
        picked += small[: n - len(picked)]
    return picked


def _probe_file(
    tsv_name: str,
    kind: str,
    tsv_path_of: Callable[[str], str],
    vps_url_hits: Callable[[list[str]], set[str]],
    config: GapConfig,
    rng: random.Random,
) -> SampleResult:
    """抽單檔 URL 查 VPS 命中，算命中率、判是否越界。"""
    urls = read_tsv_urls(tsv_path_of(tsv_name))
    total = len(urls)
    counted = total >= config.min_urls_for_verdict

    probe_urls = urls
    if config.urls_per_file_probe is not None and total > config.urls_per_file_probe:
        probe_urls = rng.sample(urls, config.urls_per_file_probe)

    hit_set = vps_url_hits(probe_urls) if probe_urls else set()
    hits = sum(1 for u in probe_urls if u in hit_set)
    probed = len(probe_urls)
    hit_rate = (hits / probed) if probed else 0.0

    breached = False
    if counted:
        if kind == "gap" and hit_rate > config.gap_hit_ceiling:
            breached = True
        elif kind == "done" and hit_rate < config.done_hit_floor:
            breached = True

    return SampleResult(
        name=tsv_name, kind=kind, total_urls=total, probed=probed,
        hits=hits, hit_rate=hit_rate, counted=counted, breached=breached,
    )


def run_sampling(
    gap_list: list[str],
    done_set: set[str],
    crawler_names: set[str],
    tsv_path_of: Callable[[str], str],
    vps_url_hits: Callable[[list[str]], set[str]],
    config: GapConfig,
    tsv_sizes_of: Optional[Callable[[list[str]], dict[str, int]]] = None,
) -> list[SampleResult]:
    """URL 級雙向抽驗。任一 counted 檔越界 → raise SamplingVerdictError（附明細）。

    - gap 抽驗宇宙 = gap_list（判缺口的檔，應幾乎不在 VPS）。
    - done 抽驗宇宙 = done-set ∩ crawler 現存檔（retention 剪掉的取不到 URL 欄）。

    ⚠ 效能鐵律（canary 2026-07-22 真機病灶，兩層都踩過）：抽樣偏向「URL≥min-N 的大檔」
      需知道每個候選檔的 size，但**取 size 的 I/O 必須攤成一次批次**：
        - 第 2 次 canary：舊版對候選全集逐個呼叫 `tsv_path_of`（scp 整檔）算 URL 數 →
          上 GB 傳輸。已改為輕量 `wc -l`（不拉檔）。
        - 第 3 次 canary：`wc -l` 雖輕量，但舊介面 `tsv_size_of(name)` 是「一次問一個」→
          run_sampling 對候選全集（真機 600+ 檔）逐個呼叫 = **600+ 次獨立 SSH 往返**，
          十幾分鐘。
      根解（方案 A）：改注入**批次 size provider** `tsv_sizes_of(names) -> {name: size}`——
      run_sampling 開頭算出候選並集（gap_list ∪ done_universe），**一次**呼叫 provider 拿
      全部 size（production wrapper 內 = 一次 SSH `wc -l file1 ... fileN`）。之後抽樣偏向的
      `size_of` 純查本地 dict、零 I/O。`tsv_path_of`（拉整檔）仍只對「最終抽中的 N 檔」在
      `_probe_file` 內呼叫。
      `tsv_sizes_of` 未注入時退回舊行為（逐檔拉整檔算 URL 數）並發 deprecated 警告——
      僅為向後相容 test/舊呼叫端；production wrapper 必須注入 `tsv_sizes_of`。
    """
    rng = random.Random(config.seed)

    done_universe = sorted(done_set & crawler_names)

    # ── 批次撈候選全集 size（一次 provider 呼叫 = 一次 SSH wc -l 全部檔）──
    # 候選宇宙 = 抽樣偏向會 size_of 到的所有檔 = gap_list ∪ done_universe。
    # 舊介面逐檔 call（600+ 次 SSH）的病灶就在這裡收口成 1 次。
    _size_cache: dict[str, int] = {}
    candidate_names = list(dict.fromkeys(gap_list + done_universe))  # 保序去重
    if tsv_sizes_of is not None:
        if candidate_names:
            try:
                sizes = tsv_sizes_of(candidate_names)
            except Exception as e:  # noqa: BLE001 — 批次 size 查失敗不致命，全當 0 檔降 advisory
                logger.warning(
                    "批次 size provider 失敗（%d 候選檔）：%s（全當 0 檔，抽樣偏向降級為隨機）",
                    len(candidate_names), e,
                )
                sizes = {}
            # provider 可能漏回某些檔（如遠端不存在）→ 缺的當 0
            _size_cache = {name: int(sizes.get(name, 0)) for name in candidate_names}

        def size_of(name: str) -> int:
            return _size_cache.get(name, 0)
    else:
        # 向後相容退路（DEPRECATED）：逐檔拉整檔算 URL 數。production 絕不該走這裡——
        # 對候選全集逐檔拉檔＝第 2 次 canary 病灶；逐檔問 size＝第 3 次 canary 病灶。
        # 只在舊 test/呼叫端未注入 tsv_sizes_of 時觸發。
        logger.warning(
            "run_sampling 未注入 tsv_sizes_of，退回『逐檔拉整檔算 URL 數』做抽樣偏向"
            "（DEPRECATED，對候選全集逐檔拉檔＝canary 效能病灶）。"
            "production wrapper 必須注入 tsv_sizes_of（一次 SSH 批次 wc -l 不拉檔）。"
        )

        def size_of(name: str) -> int:
            if name not in _size_cache:
                try:
                    _size_cache[name] = len(read_tsv_urls(tsv_path_of(name)))
                except OSError as e:
                    logger.warning("抽樣偏向計數讀檔失敗 %s：%s（當 0 檔）", name, e)
                    _size_cache[name] = 0
            return _size_cache[name]

    gap_sample = _pick_sample(
        gap_list, config.sample_gap_n, size_of, config.min_urls_for_verdict, rng
    )
    done_sample = _pick_sample(
        done_universe, config.sample_done_n, size_of, config.min_urls_for_verdict, rng
    )

    results: list[SampleResult] = []
    for name in gap_sample:
        results.append(_probe_file(name, "gap", tsv_path_of, vps_url_hits, config, rng))
    for name in done_sample:
        results.append(_probe_file(name, "done", tsv_path_of, vps_url_hits, config, rng))

    # 印報告（stdout/log）
    logger.info("URL 級雙向抽驗報告（抽 gap=%d / done=%d 檔）：",
                len(gap_sample), len(done_sample))
    for r in results:
        logger.info(r.line())

    breached = [r for r in results if r.breached]
    if breached:
        detail = "\n".join(r.line() for r in results)
        raise SamplingVerdictError(
            "URL 級抽驗越界，本輪 abort（教訓 #3 雙向抽驗防線）：\n"
            f"越界檔 {len(breached)} 個。逐檔明細：\n{detail}\n"
            f"判準：gap 檔命中率須 ≤ {config.gap_hit_ceiling:.0%}、"
            f"done 檔須 ≥ {config.done_hit_floor:.0%}（僅 URL≥{config.min_urls_for_verdict} 檔計入）。"
        )
    return results


# ═══════════════════════════════════════════════════════════════════════════
# (source,date) advisory（bug (b) 降級：只報不擋）
# ═══════════════════════════════════════════════════════════════════════════


def advisory_source_date(
    gap_list: list[str],
    config: GapConfig,
) -> None:
    """(source,date) 聚合 advisory log（只報不擋）。

    G9：(source,date) 比對已從主判準降級為 advisory——批次日 ≠ 發布日，日期語義比對
    每週界必產跨桶誤差，不可當 fail-closed 判準（會誤殺正常輪）。這裡只印聚合摘要供
    人工觀察，不影響 exit code。順帶觸發 source 映射 fail-loud（未知 source 在此暴露）。
    """
    from collections import defaultdict

    by_source: dict[str, int] = defaultdict(int)
    for name in gap_list:
        short = parse_source_shortname(name)
        full = map_source(short, config.source_map)  # 未知 source → fail-loud
        by_source[full] += 1
    if by_source:
        logger.info("(source,date) advisory（gap 檔按 VPS source 全名聚合，只報不擋）：")
        for full, cnt in sorted(by_source.items()):
            logger.info("  %s: %d 檔", full, cnt)


# ═══════════════════════════════════════════════════════════════════════════
# 原子寫出（tmp→mv）
# ═══════════════════════════════════════════════════════════════════════════


def atomic_write_lines(out_path: str, lines: list[str]) -> None:
    """tmp→mv 原子寫（G9：per-run 檔原子寫，launcher 只吃完整檔）。"""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=out.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)  # 原子 rename
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ═══════════════════════════════════════════════════════════════════════════
# 主流程（可測核心：全依賴注入）
# ═══════════════════════════════════════════════════════════════════════════


def generate_gap(
    *,
    list_crawler_tsvs: Callable[[], list[TsvEntry]],
    done_set: set[str],
    skip_set: set[str],
    tsv_path_of: Callable[[str], str],
    vps_url_hits: Callable[[list[str]], set[str]],
    now_epoch: float,
    config: GapConfig,
    tsv_sizes_of: Optional[Callable[[list[str]], dict[str, int]]] = None,
) -> list[str]:
    """算本輪 gap 清單並跑抽驗。回傳 gap 檔名清單（可能為空＝本週無新工作）。

    fail-closed：任何步驟 raise GapGenError → 呼叫端非零退出、絕不 fallback 舊清單。

    參數全為注入（純資料 / callable），unit test 不碰網路。

    `tsv_sizes_of`（**批次** size provider：一次吃候選檔名清單、回 {name: size} dict，
    production wrapper 內 = 一次 SSH `wc -l file1 ... fileN` 不拉檔）：抽樣偏向「大檔」用。
    **production wrapper 必須注入**——否則 run_sampling 退回「逐檔拉整檔算 URL 數」（canary
    病灶：真機 tsv_path_of=scp 整檔，全集逐檔拉 = 上 GB 傳輸 + 600+ 次往返）。
    改為批次介面（而非舊 `tsv_size_of` 逐檔 callable）正是第 3 次 canary 的根解：逐檔問
    size = 600+ 次獨立 SSH，批次一次 SSH 收口。未注入時退回舊行為並發 deprecated 警告，
    僅為向後相容 test/舊呼叫端。
    """
    # 1. 列 crawler TSV（含 mtime）——注入
    try:
        crawler_tsvs = list_crawler_tsvs()
    except GapGenError:
        raise
    except Exception as e:  # 網路/SSH 層錯誤統一歸 fail-closed
        raise CrawlerListError(f"列 crawler TSV 清單失敗：{e}") from e

    crawler_names = {e.name for e in crawler_tsvs if _tsv_only(e.name)}
    logger.info(
        "輸入：crawler TSV %d / done-set %d / skip-set %d",
        len(crawler_names), len(done_set), len(skip_set),
    )

    # 2. 主差集 + mtime 排除
    gap_list = compute_gap(crawler_tsvs, done_set, skip_set, now_epoch, config)
    logger.info("主差集後 gap = %d 檔", len(gap_list))

    # 3. (source,date) advisory（含 source 映射 fail-loud）
    advisory_source_date(gap_list, config)

    # 4. 清單為空＝正常退出（本週無新工作，合法狀態，非 abort）
    if not gap_list:
        logger.info("本輪 gap 為空——本週無新工作，正常退出（exit 0）")
        return gap_list

    # 5. URL 級雙向抽驗（wrap VPS 查詢錯誤為 fail-closed）
    def _safe_hits(urls: list[str]) -> set[str]:
        try:
            return vps_url_hits(urls)
        except GapGenError:
            raise
        except Exception as e:
            raise VpsQueryError(f"查 VPS URL 命中失敗：{e}") from e

    run_sampling(
        gap_list, done_set, crawler_names, tsv_path_of, _safe_hits, config,
        tsv_sizes_of=tsv_sizes_of,
    )

    logger.info("抽驗全過，本輪 gap 清單 %d 檔確認", len(gap_list))
    return gap_list


# ═══════════════════════════════════════════════════════════════════════════
# CLI（launcher 呼叫；production 端在此接 SSH/tunnel 的真實注入）
# ═══════════════════════════════════════════════════════════════════════════


def _line_alert(msg: str) -> None:
    """CRITICAL 告警落地點。gap_gen 跑在 orchestrator VM——實際 LINE 推送由 launcher
    捕捉非零退出後發（見 plan G11 告警集中 launcher/watchdog 層）。此處僅印 CRITICAL
    到 stderr/log 供 launcher 擷取、不自接 LINE（面最小化）。
    """
    logger.critical(msg)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口。回傳 exit code（0 正常 / 非零 fail-closed）。

    production 注入（SSH 列檔 / tunnel 查 VPS）在此組裝——但那些是 I/O 邊界，
    真正邏輯全在 generate_gap（已 unit test 覆蓋）。此 main 的 I/O 注入由 launcher
    在 orchestrator VM 上提供對應 helper（SSH crawler / tunnel + indexer_import SELECT）。
    """
    parser = argparse.ArgumentParser(description="Dynamic gap generator (Plan B v8.1 G9)")
    parser.add_argument("--out", required=True, help="per-run 輸出路徑 gap_<launcher_run_id>.txt")
    parser.add_argument("--done-set", default="/data/indexed_tsvs.txt")
    parser.add_argument("--skip-set", default="/data/indexed_skipped.txt")
    # production I/O 注入的細節（crawler host / tunnel DSN 等）由 launcher 經環境傳入；
    # 此 CLI 保留 --out 契約，真實注入在 orchestrator VM 端組裝（見 launcher 接線）。
    args = parser.parse_args(argv)

    logger.error(
        "gap_gen.main 的 production I/O 注入（SSH 列 crawler TSV + tunnel 查 VPS）"
        "尚未在此 CLI 接線——launcher 端負責提供 list_crawler_tsvs / vps_url_hits "
        "並呼叫 generate_gap()。此為刻意的邊界：邏輯核心已測，I/O 由 launcher 組裝。"
    )
    # 不靜默假成功：I/O 未接線時 fail-closed 退出，絕不寫空/舊清單。
    _line_alert("gap_gen CLI I/O 未接線，fail-closed 退出（非零）")
    _ = (args.out, args.done_set, args.skip_set)
    return 2


if __name__ == "__main__":
    sys.exit(main())
