"""
準備 POC 測試資料 - 從現有 TSV 選取 20 篇不同長度的文章

使用方式：
    python -m indexing.prepare_test_data
"""
import json
import os
from pathlib import Path
from typing import List, Tuple
import random


def parse_tsv_file(tsv_path: str, max_lines: int = 500) -> List[Tuple[str, dict]]:
    """讀取 TSV 檔案，返回 (url, json_data) 列表"""
    articles = []

    with open(tsv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 跳過 header
    start = 1 if lines and lines[0].strip().startswith('url\t') else 0

    for line in lines[start:start + max_lines]:
        try:
            parts = line.strip().split('\t', 1)
            if len(parts) == 2:
                url = parts[0]
                json_data = json.loads(parts[1])
                body = json_data.get('articleBody', '')
                if body and len(body) >= 100:  # 至少 100 字
                    articles.append((url, json_data, len(body)))
        except Exception:
            continue

    return articles


def select_articles_by_length(
    articles: List[Tuple[str, dict, int]],
    short_count: int = 5,
    medium_count: int = 10,
    long_count: int = 5
) -> List[Tuple[str, dict]]:
    """
    按長度分類選取文章

    短文：< 500 字
    中等：500-1500 字
    長文：> 1500 字
    """
    short = [(url, data) for url, data, length in articles if length < 500]
    medium = [(url, data) for url, data, length in articles if 500 <= length <= 1500]
    long = [(url, data) for url, data, length in articles if length > 1500]

    print(f"找到文章分布: 短文 {len(short)}, 中等 {len(medium)}, 長文 {len(long)}")

    selected = []

    # 隨機選取
    random.seed(42)  # 固定種子確保可重現

    if len(short) >= short_count:
        selected.extend(random.sample(short, short_count))
    else:
        selected.extend(short)
        print(f"警告: 短文只有 {len(short)} 篇，不足 {short_count} 篇")

    if len(medium) >= medium_count:
        selected.extend(random.sample(medium, medium_count))
    else:
        selected.extend(medium)
        print(f"警告: 中等長度只有 {len(medium)} 篇，不足 {medium_count} 篇")

    if len(long) >= long_count:
        selected.extend(random.sample(long, long_count))
    else:
        selected.extend(long)
        print(f"警告: 長文只有 {len(long)} 篇，不足 {long_count} 篇")

    return selected


def main():
    # 專案根目錄
    project_root = Path(__file__).parent.parent.parent.parent
    crawled_dir = project_root / "crawled"
    output_dir = project_root / "crawled"

    # 要讀取的 TSV 檔案
    tsv_files = [
        crawled_dir / "已上傳" / "cna_2025_12.tsv",
        crawled_dir / "已上傳" / "ltn_2025_12.tsv",
        crawled_dir / "udn_2025_12.tsv",
        crawled_dir / "已上傳" / "test_5_articles.tsv",
    ]

    all_articles = []

    for tsv_path in tsv_files:
        if tsv_path.exists():
            print(f"讀取: {tsv_path.name}")
            articles = parse_tsv_file(str(tsv_path), max_lines=200)
            print(f"  載入 {len(articles)} 篇")
            all_articles.extend(articles)
        else:
            print(f"跳過（不存在）: {tsv_path}")

    print(f"\n總共載入: {len(all_articles)} 篇文章")

    # 選取 20 篇
    selected = select_articles_by_length(all_articles)

    print(f"\n選取 {len(selected)} 篇文章:")
    for i, (url, data) in enumerate(selected):
        body_len = len(data.get('articleBody', ''))
        headline = data.get('headline', '')[:40]
        category = "短" if body_len < 500 else ("中" if body_len <= 1500 else "長")
        print(f"  {i+1}. [{category}] {body_len:4d}字 - {headline}...")

    # 寫入輸出檔案
    output_path = output_dir / "poc_test_20.tsv"

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("url\tjson_data\n")
        for url, data in selected:
            f.write(f"{url}\t{json.dumps(data, ensure_ascii=False)}\n")

    print(f"\n已儲存到: {output_path}")


if __name__ == "__main__":
    main()
