"""gap_gen 動態差集 gap 生成器測試（Plan B v8.1 G9）。

**紀律**：本套件為純邏輯測試，全依賴注入（假 crawler 清單 / 假 done-set / 假 URL
命中），**不碰網路、不需 docker/testcontainers**——故絕不 SKIP，PASSED 計數即真綠
（教訓 #2：skipif 全跳＝假綠）。用 `uv run pytest code/python/tests/test_gap_gen.py -v`
跑，驗 PASSED 計數非退出碼。

**必含教訓 #3（docs/crawling-lessons-0712.md #3）兩個歷史 bug 案例**：
  (a) 短名/全名映射：`chinatimes`（檔名）vs `chinatimes.com`（VPS source）不 mismatch。
  (b) 抽驗層用 URL 級不用 (source,date)：餵「批次日 ≠ 發布日」的資料，驗不誤 abort。
加：空清單正常退出 / 映射缺 fail-loud / 抽驗越界 abort / mtime 排除。
"""
from __future__ import annotations

import time

import pytest

from indexing import gap_gen
from indexing.gap_gen import (
    GapConfig,
    GapGenError,
    SamplingVerdictError,
    SourceMappingError,
    TsvEntry,
    compute_gap,
    generate_gap,
    map_source,
    parse_source_shortname,
)


# ═══════════════════════════════════════════════════════════════════════════
# 測試工具：可注入的假 crawler / VPS
# ═══════════════════════════════════════════════════════════════════════════

_NOW = 1_800_000_000.0  # 固定 now（epoch）讓 mtime 測試確定


def _old(name: str, minutes_ago: float = 999.0) -> TsvEntry:
    """一個 mtime 夠舊（不被排除）的 crawler TSV entry。"""
    return TsvEntry(name=name, mtime_epoch=_NOW - minutes_ago * 60)


class FakeCrawler:
    """假 crawler：檔名 → URL 清單。tsv_path_of 直接回檔名當 key。"""

    def __init__(self, files: dict[str, list[str]]):
        self._files = files

    def tsv_path_of(self, name: str) -> str:
        return name  # test 裡 path＝name（read_tsv_urls 被 monkeypatch）

    def urls_of(self, name: str) -> list[str]:
        return self._files.get(name, [])


def _install_fake_read(monkeypatch, crawler: FakeCrawler):
    """monkeypatch read_tsv_urls，讓它從 FakeCrawler 取 URL（不讀真檔）。"""
    monkeypatch.setattr(gap_gen, "read_tsv_urls", lambda path: crawler.urls_of(path))


def _hits_factory(vps_urls: set[str]):
    """回傳 vps_url_hits callable：查一批 URL 在 VPS 的命中集合。"""
    def hits(urls: list[str]) -> set[str]:
        return {u for u in urls if u in vps_urls}
    return hits


# ═══════════════════════════════════════════════════════════════════════════
# 檔名解析 + source 映射（bug (a) 直接單元）
# ═══════════════════════════════════════════════════════════════════════════


class TestSourceShortnameParsing:
    def test_standard_stamp(self):
        assert parse_source_shortname("chinatimes_2026-07-15_03-12.tsv") == "chinatimes"

    def test_source_with_underscore(self):
        # esg_businesstoday 短名含底線，不可 split('_')[0]
        assert parse_source_shortname("esg_businesstoday_2026-07-15_03-12.tsv") == "esg_businesstoday"

    def test_chunk_by_month(self):
        assert parse_source_shortname("cna_2026-07.tsv") == "cna"

    def test_part_suffix(self):
        assert parse_source_shortname("udn_2026-07-15_03-12_part003.tsv") == "udn"

    def test_all_sources(self):
        for short in ("chinatimes", "cna", "ltn", "udn", "moea", "einfo", "esg_businesstoday"):
            assert parse_source_shortname(f"{short}_2026-07-15_03-12.tsv") == short


class TestSourceMapping:
    def test_bug_a_shortname_maps_to_fullname(self):
        """教訓 #3 bug (a)：chinatimes（檔名短名）↔ chinatimes.com（VPS 全名）不 mismatch。

        舊算法直接拿 'chinatimes' 比 VPS 'chinatimes.com' → 永遠 miss → 全算缺口。
        映射表把兩端同構。
        """
        cfg = GapConfig()
        assert map_source("chinatimes", cfg.source_map) == "chinatimes.com"
        assert map_source("cna", cfg.source_map) == "cna.com.tw"
        assert map_source("esg_businesstoday", cfg.source_map) == "esg.businesstoday.com.tw"

    def test_unknown_source_fail_loud(self):
        """新 source 查無映射 → fail-loud abort（onboarding 防線，不靜默）。"""
        cfg = GapConfig()
        with pytest.raises(SourceMappingError) as ei:
            map_source("newpaper", cfg.source_map)
        assert "newpaper" in str(ei.value)


# ═══════════════════════════════════════════════════════════════════════════
# 主差集 + mtime 排除
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeGap:
    def test_basic_diff(self):
        crawler = [
            _old("chinatimes_2026-07-15_03-12.tsv"),
            _old("cna_2026-07-15_03-12.tsv"),
            _old("udn_2026-07-15_03-12.tsv"),
        ]
        done = {"chinatimes_2026-07-15_03-12.tsv"}
        skip = {"cna_2026-07-15_03-12.tsv"}
        gap = compute_gap(crawler, done, skip, _NOW, GapConfig())
        assert gap == ["udn_2026-07-15_03-12.tsv"]

    def test_skip_set_subtracted(self):
        """skip-set（G11 契約）確實被扣除。"""
        crawler = [_old("a_2026-07-15_03-12.tsv"), _old("b_2026-07-15_03-12.tsv")]
        cfg = GapConfig(source_map={"a": "a.com", "b": "b.com"})
        gap = compute_gap(crawler, set(), {"a_2026-07-15_03-12.tsv"}, _NOW, cfg)
        assert gap == ["b_2026-07-15_03-12.tsv"]

    def test_mtime_exclude_fresh_file(self):
        """mtime < 60 分的 TSV 不進清單（防 crawler 寫入中半檔）。"""
        crawler = [
            _old("old_2026-07-15_03-12.tsv", minutes_ago=120),   # 進
            _old("fresh_2026-07-15_09-59.tsv", minutes_ago=10),   # 排除
        ]
        cfg = GapConfig(mtime_exclude_minutes=60)
        gap = compute_gap(crawler, set(), set(), _NOW, cfg)
        assert gap == ["old_2026-07-15_03-12.tsv"]

    def test_ignores_non_tsv_sidecar(self):
        """.tsv.pg_checkpoint.json 等 sidecar 不算 TSV。"""
        crawler = [
            _old("chinatimes_2026-07-15_03-12.tsv"),
            _old("chinatimes_2026-07-15_03-12.tsv.pg_checkpoint.json"),
        ]
        gap = compute_gap(crawler, set(), set(), _NOW, GapConfig())
        assert gap == ["chinatimes_2026-07-15_03-12.tsv"]


# ═══════════════════════════════════════════════════════════════════════════
# skip-set / done-set / URL 讀取 helper
# ═══════════════════════════════════════════════════════════════════════════


class TestFileHelpers:
    def test_load_skip_set_missing_file_empty(self, tmp_path):
        """缺 skip 檔＝空 set（首次上線正常態，不 raise）。"""
        assert gap_gen.load_skip_set(str(tmp_path / "nope.txt")) == set()

    def test_load_skip_set_strips_cr(self, tmp_path):
        """教訓 #7：\\r 清洗（Windows→Linux 傳檔防線）。"""
        p = tmp_path / "skipped.txt"
        p.write_bytes(b"a.tsv\r\nb.tsv\n\n  c.tsv  \n")
        assert gap_gen.load_skip_set(str(p)) == {"a.tsv", "b.tsv", "c.tsv"}

    def test_read_tsv_urls_col0(self, tmp_path):
        """URL 欄＝col 0（tab 分隔第一欄，比照 cloud_embed.parse_tsv_line）。"""
        p = tmp_path / "x.tsv"
        p.write_text(
            'https://www.chinatimes.com/a\t{"headline":"x"}\n'
            'https://www.chinatimes.com/b\t{"headline":"y"}\n',
            encoding="utf-8",
        )
        assert gap_gen.read_tsv_urls(str(p)) == [
            "https://www.chinatimes.com/a",
            "https://www.chinatimes.com/b",
        ]

    def test_read_tsv_urls_skips_non_url_line(self, tmp_path):
        """URL 欄 schema 破壞（非 http 開頭）→ 跳過該行、不 silent、不 crash。"""
        p = tmp_path / "x.tsv"
        p.write_text(
            'https://good.com/a\t{"h":1}\n'
            'garbage-no-scheme\t{"h":2}\n',
            encoding="utf-8",
        )
        assert gap_gen.read_tsv_urls(str(p)) == ["https://good.com/a"]


# ═══════════════════════════════════════════════════════════════════════════
# 端到端：generate_gap（全注入）
# ═══════════════════════════════════════════════════════════════════════════


def _gen(monkeypatch, *, files, done, skip, vps_urls, config=None, now=_NOW):
    """跑 generate_gap 的測試 harness。"""
    crawler = FakeCrawler(files)
    _install_fake_read(monkeypatch, crawler)
    cfg = config or GapConfig(seed=42)
    return generate_gap(
        list_crawler_tsvs=lambda: [_old(n) for n in files],
        done_set=set(done),
        skip_set=set(skip),
        tsv_path_of=crawler.tsv_path_of,
        vps_url_hits=_hits_factory(set(vps_urls)),
        now_epoch=now,
        config=cfg,
    )


class TestGenerateGapEndToEnd:

    def test_empty_gap_normal_exit(self, monkeypatch):
        """清單為空＝正常退出（非 abort）。所有 crawler 檔都已在 done-set。"""
        files = {"chinatimes_2026-07-15_03-12.tsv": ["https://www.chinatimes.com/a"]}
        gap = _gen(
            monkeypatch,
            files=files,
            done={"chinatimes_2026-07-15_03-12.tsv"},
            skip=set(),
            vps_urls={"https://www.chinatimes.com/a"},
        )
        assert gap == []  # 空清單，正常回傳（呼叫端 exit 0）

    def test_happy_path_gap_and_done_verify_pass(self, monkeypatch):
        """正常一輪：gap 檔幾乎不在 VPS（命中低）、done 檔幾乎全在（命中高）→ 抽驗過。"""
        gap_urls = [f"https://www.chinatimes.com/gap/{i}" for i in range(50)]
        done_urls = [f"https://www.chinatimes.com/done/{i}" for i in range(50)]
        files = {
            "chinatimes_2026-07-15_03-12.tsv": gap_urls,   # gap（不在 VPS）
            "chinatimes_2026-07-14_03-12.tsv": done_urls,  # done（在 VPS）
        }
        # VPS 有全部 done_urls，無任何 gap_urls
        gap = _gen(
            monkeypatch,
            files=files,
            done={"chinatimes_2026-07-14_03-12.tsv"},
            skip=set(),
            vps_urls=set(done_urls),
        )
        assert gap == ["chinatimes_2026-07-15_03-12.tsv"]

    def test_bug_b_url_level_not_source_date(self, monkeypatch):
        """教訓 #3 bug (b)：抽驗用 URL 級不用 (source,date)。

        餵「批次日 ≠ 發布日」的資料：一個 gap 檔的檔名批次戳是 07-15，但裡面文章的
        發布日橫跨多天（URL 帶不同日期）。若抽驗照舊用 (source,date) 聚合比對 VPS 的
        date_published，跨桶必誤判 → 誤 abort。URL 級抽驗只看「這些 URL 在不在 VPS」，
        與日期語義無關 → 不誤 abort。

        本 case：gap 檔文章 URL 帶跨日日期（模擬批次日≠發布日），VPS 完全沒這些 URL
        （真缺口）→ 命中 0% ≤ 上水位 → 正常過，不 abort。
        """
        # 檔名批次戳 07-15，但 URL path 帶不同「發布日」（07-10 ~ 07-14）——
        # 這正是教訓 #3 連帶記載的「批次日≠發布日」形狀。
        gap_urls = [
            f"https://www.chinatimes.com/realtimenews/2026071{d}00000{i}-260402"
            for d in range(0, 5) for i in range(10)
        ]  # 50 個 URL，橫跨 07-10~07-14
        done_urls = [f"https://www.chinatimes.com/done/{i}" for i in range(50)]
        files = {
            "chinatimes_2026-07-15_03-12.tsv": gap_urls,   # 批次日 07-15、內含跨日文章
            "chinatimes_2026-07-14_03-12.tsv": done_urls,
        }
        gap = _gen(
            monkeypatch,
            files=files,
            done={"chinatimes_2026-07-14_03-12.tsv"},
            skip=set(),
            vps_urls=set(done_urls),  # gap 檔的跨日 URL 一個都不在 VPS → 真缺口
        )
        # URL 級：命中 0%，不因日期跨桶誤 abort
        assert gap == ["chinatimes_2026-07-15_03-12.tsv"]

    def test_sampling_breach_gap_file_actually_indexed(self, monkeypatch):
        """抽驗越界 abort：一個「判缺口」的檔其實 URL 全在 VPS（命中率高越上水位）。

        這正是教訓 #3 舊清單的病灶——把 VPS 明明有資料的檔算成缺口（會白做重埋錢）。
        URL 級雙向抽驗把它攔下。
        """
        gap_urls = [f"https://www.chinatimes.com/g/{i}" for i in range(50)]
        files = {"chinatimes_2026-07-15_03-12.tsv": gap_urls}
        with pytest.raises(SamplingVerdictError) as ei:
            _gen(
                monkeypatch,
                files=files,
                done=set(),
                skip=set(),
                vps_urls=set(gap_urls),  # gap 檔 URL 全在 VPS → 命中 100% >> 上水位
            )
        assert "越界" in str(ei.value)

    def test_sampling_breach_done_file_actually_missing(self, monkeypatch):
        """抽驗越界 abort：一個「判已有」的 done 檔其實 URL 都不在 VPS（命中率低於下水位）。

        表示 done-set 記錄與實況不符（教訓 #3 「done-set 污染」的反向）。
        """
        gap_urls = [f"https://www.chinatimes.com/g/{i}" for i in range(50)]
        done_urls = [f"https://www.chinatimes.com/d/{i}" for i in range(50)]
        files = {
            "chinatimes_2026-07-15_03-12.tsv": gap_urls,
            "chinatimes_2026-07-14_03-12.tsv": done_urls,  # 標 done 但 VPS 沒有
        }
        with pytest.raises(SamplingVerdictError):
            _gen(
                monkeypatch,
                files=files,
                done={"chinatimes_2026-07-14_03-12.tsv"},
                skip=set(),
                vps_urls=set(),  # 連 done 檔的 URL 都不在 VPS → done 命中 0% < 下水位
            )

    def test_small_file_advisory_not_counted(self, monkeypatch):
        """單檔 URL < min-N 只記 advisory 不納判準（不誤 abort）。

        一個小 gap 檔（3 URL）碰巧 URL 都在 VPS（re-crawl），若納判準會誤 abort；
        因 < min_urls_for_verdict 只當 advisory → 不 abort。
        """
        small_gap = ["https://www.chinatimes.com/g/1",
                     "https://www.chinatimes.com/g/2",
                     "https://www.chinatimes.com/g/3"]
        files = {"chinatimes_2026-07-15_03-12.tsv": small_gap}
        cfg = GapConfig(seed=1, min_urls_for_verdict=20)
        gap = _gen(
            monkeypatch,
            files=files,
            done=set(),
            skip=set(),
            vps_urls=set(small_gap),  # 小檔全命中，但因 <min-N 不算越界
            config=cfg,
        )
        assert gap == ["chinatimes_2026-07-15_03-12.tsv"]

    def test_tsv_path_of_called_only_for_picked_files_not_whole_set(self, monkeypatch):
        """canary 2026-07-22 效能病灶回歸防線：抽樣偏向不得對候選全集拉整檔。

        病灶（第 2 次 canary）：run_sampling 的抽樣偏向對『所有候選檔』（gap 全集 +
        done∩crawler 全集，真機 300+ 檔）呼叫 tsv_path_of 算 URL 數；真機 tsv_path_of=scp
        整檔 → 上 GB 傳輸。
        修法：注入輕量 size 查詢（遠端 wc -l 不拉檔）判『大檔』，tsv_path_of（拉整檔）只對
        『最終抽中的 N 檔』呼叫。

        本 test 用 counter 記 tsv_path_of 被呼叫次數，斷言 ≤ 2N（gap N + done N），而非
        候選全集數（本 case 40 檔）。size 查詢改走批次 tsv_sizes_of（見下方 provider
        呼叫次數 test）。
        """
        n_candidates = 20  # gap 20 檔 + done 20 檔 = 40 候選，遠 > 2N=12
        gap_files = {
            f"chinatimes_2026-07-15_03-{i:02d}.tsv":
                [f"https://www.chinatimes.com/gap/{i}/{j}" for j in range(50)]
            for i in range(n_candidates)
        }
        done_files = {
            f"chinatimes_2026-07-14_03-{i:02d}.tsv":
                [f"https://www.chinatimes.com/done/{i}/{j}" for j in range(50)]
            for i in range(n_candidates)
        }
        all_files = {**gap_files, **done_files}
        crawler = FakeCrawler(all_files)
        _install_fake_read(monkeypatch, crawler)

        path_calls: list[str] = []

        def counting_path_of(name: str) -> str:
            path_calls.append(name)   # 真機這裡 = scp 整檔（貴）
            return name

        def batch_sizes_of(names) -> dict[str, int]:
            # 真機這裡 = 一次 SSH `wc -l file1 ... fileN`（便宜、不拉檔、一次往返）
            return {n: len(crawler.urls_of(n)) for n in names}

        # VPS 有全部 done URL、無 gap URL → 抽驗全過（不 abort）
        all_done_urls = {u for urls in done_files.values() for u in urls}

        cfg = GapConfig(seed=7, sample_gap_n=3, sample_done_n=3)
        gap = generate_gap(
            list_crawler_tsvs=lambda: [_old(n) for n in all_files],
            done_set=set(done_files),
            skip_set=set(),
            tsv_path_of=counting_path_of,
            vps_url_hits=_hits_factory(all_done_urls),
            now_epoch=_NOW,
            config=cfg,
            tsv_sizes_of=batch_sizes_of,
        )
        # 抽驗過，gap 檔全數回傳（20 檔）
        assert len(gap) == n_candidates

        # 核心斷言：tsv_path_of（拉整檔）只對抽中的 N 檔呼叫，不是候選全集
        n_total = cfg.sample_gap_n + cfg.sample_done_n  # 6
        assert len(path_calls) <= n_total, (
            f"tsv_path_of 被呼叫 {len(path_calls)} 次（應 ≤ {n_total} = 抽中檔數）；"
            f"若接近候選全集 {len(all_files)}=canary 病灶復發（對全集拉整檔）"
        )

    def test_size_provider_called_once_not_per_candidate(self, monkeypatch):
        """canary 2026-07-22 第 3 次效能病灶回歸防線：size 查詢必須批次化為單次呼叫。

        病灶：舊介面 `tsv_size_of(name)` 一次問一個 → run_sampling 對候選全集（真機 600+ 檔）
        逐個呼叫 = 600+ 次獨立 SSH 往返（十幾分鐘）。
        根解（方案 A）：改注入批次 provider `tsv_sizes_of(names) -> dict`，run_sampling 開頭
        **一次**把候選並集交來、拿全部 size。

        本 test 用 counter 記 provider **被呼叫次數**，斷言 == 1（而非候選檔數 40）——這是
        「逐檔 SSH」復發時會立刻紅的防線。並斷言那唯一一次收到的檔名集 == 候選並集
        （gap_list ∪ done_universe），確認批次涵蓋全集（不是只批一半又逐檔補）。
        """
        n_candidates = 20  # gap 20 + done 20 = 40 候選，遠 > 1 次
        gap_files = {
            f"chinatimes_2026-07-15_03-{i:02d}.tsv":
                [f"https://www.chinatimes.com/gap/{i}/{j}" for j in range(50)]
            for i in range(n_candidates)
        }
        done_files = {
            f"chinatimes_2026-07-14_03-{i:02d}.tsv":
                [f"https://www.chinatimes.com/done/{i}/{j}" for j in range(50)]
            for i in range(n_candidates)
        }
        all_files = {**gap_files, **done_files}
        crawler = FakeCrawler(all_files)
        _install_fake_read(monkeypatch, crawler)

        provider_call_batches: list[list[str]] = []

        def counting_batch_sizes_of(names) -> dict[str, int]:
            names = list(names)
            provider_call_batches.append(names)   # 記每次呼叫收到的檔名清單
            return {n: len(crawler.urls_of(n)) for n in names}

        all_done_urls = {u for urls in done_files.values() for u in urls}
        cfg = GapConfig(seed=7, sample_gap_n=3, sample_done_n=3)
        gap = generate_gap(
            list_crawler_tsvs=lambda: [_old(n) for n in all_files],
            done_set=set(done_files),
            skip_set=set(),
            tsv_path_of=lambda n: n,
            vps_url_hits=_hits_factory(all_done_urls),
            now_epoch=_NOW,
            config=cfg,
            tsv_sizes_of=counting_batch_sizes_of,
        )
        assert len(gap) == n_candidates

        # 核心斷言：provider 只被呼叫「1 次」（而非候選檔數 40）——逐檔 SSH 復發防線
        assert len(provider_call_batches) == 1, (
            f"批次 size provider 被呼叫 {len(provider_call_batches)} 次（應 == 1）；"
            f"若接近候選全集 {len(all_files)}=第 3 次 canary 病灶復發（逐檔 SSH 往返）"
        )
        # 那唯一一次收到的必須是「候選並集」（gap_list ∪ done_universe），涵蓋全集
        expected_candidates = set(gap_files) | set(done_files)
        assert set(provider_call_batches[0]) == expected_candidates, (
            "批次 provider 應一次收到候選並集全集（gap_list ∪ done_universe），"
            "不得只批一半或分批"
        )

    def test_deprecated_fallback_when_no_size_provider(self, monkeypatch, caplog):
        """未注入 tsv_sizes_of → 退回舊行為（逐檔拉檔算 URL 數）+ 發 deprecated 警告。

        向後相容退路：舊 test/呼叫端未注入 tsv_sizes_of 時仍能跑，但 production 絕不該走這裡
        （會對候選全集逐檔拉檔）。斷言警告有發（不靜默降級）。
        """
        gap_urls = [f"https://www.chinatimes.com/g/{i}" for i in range(50)]
        files = {"chinatimes_2026-07-15_03-12.tsv": gap_urls}
        import logging
        with caplog.at_level(logging.WARNING, logger="indexing.gap_gen"):
            gap = _gen(  # _gen 不傳 tsv_sizes_of → 走退路
                monkeypatch,
                files=files,
                done=set(),
                skip=set(),
                vps_urls=set(),  # gap 檔不在 VPS → 抽驗過
            )
        assert gap == ["chinatimes_2026-07-15_03-12.tsv"]
        assert any("tsv_sizes_of" in r.message and "DEPRECATED" in r.message
                   for r in caplog.records), "未注入 tsv_sizes_of 應發 deprecated 警告"

    def test_unknown_source_fail_loud_in_pipeline(self, monkeypatch):
        """端到端：gap 含未知 source → advisory 階段 fail-loud abort（非零退出源）。"""
        files = {"unknownpaper_2026-07-15_03-12.tsv": ["https://unknownpaper.io/a"]}
        with pytest.raises(SourceMappingError):
            _gen(
                monkeypatch,
                files=files,
                done=set(),
                skip=set(),
                vps_urls=set(),
            )

    def test_crawler_list_failure_fail_closed(self, monkeypatch):
        """列 crawler TSV 失敗 → fail-closed（CrawlerListError，非零退出）。"""
        def boom():
            raise RuntimeError("ssh crawler timeout")
        with pytest.raises(GapGenError):
            generate_gap(
                list_crawler_tsvs=boom,
                done_set=set(),
                skip_set=set(),
                tsv_path_of=lambda n: n,
                vps_url_hits=lambda urls: set(),
                now_epoch=_NOW,
                config=GapConfig(),
            )

    def test_vps_query_failure_fail_closed(self, monkeypatch):
        """查 VPS 命中失敗 → fail-closed（VpsQueryError，絕不 fallback 舊清單）。"""
        gap_urls = [f"https://www.chinatimes.com/g/{i}" for i in range(50)]
        files = {"chinatimes_2026-07-15_03-12.tsv": gap_urls}
        crawler = FakeCrawler(files)
        _install_fake_read(monkeypatch, crawler)

        def boom_hits(urls):
            raise RuntimeError("tunnel down")

        with pytest.raises(GapGenError):
            generate_gap(
                list_crawler_tsvs=lambda: [_old(n) for n in files],
                done_set=set(),
                skip_set=set(),
                tsv_path_of=crawler.tsv_path_of,
                vps_url_hits=boom_hits,
                now_epoch=_NOW,
                config=GapConfig(seed=1),
            )


# ═══════════════════════════════════════════════════════════════════════════
# 原子寫出
# ═══════════════════════════════════════════════════════════════════════════


class TestAtomicWrite:
    def test_atomic_write_content(self, tmp_path):
        out = tmp_path / "gap_run123.txt"
        gap_gen.atomic_write_lines(str(out), ["a.tsv", "b.tsv"])
        assert out.read_text(encoding="utf-8") == "a.tsv\nb.tsv\n"

    def test_atomic_write_no_partial_tmp_left(self, tmp_path):
        """成功寫後不留 .tmp 殘檔。"""
        out = tmp_path / "gap_run123.txt"
        gap_gen.atomic_write_lines(str(out), ["x.tsv"])
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []


# ═══════════════════════════════════════════════════════════════════════════
# CLI fail-closed 契約
# ═══════════════════════════════════════════════════════════════════════════


class TestCliFailClosed:
    def test_main_returns_nonzero_when_io_not_wired(self):
        """CLI I/O 未接線時 fail-closed 回非零（不靜默假成功、不寫空/舊清單）。"""
        rc = gap_gen.main(["--out", "gap_x.txt"])
        assert rc != 0
