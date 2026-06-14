"""
POC 來源分析 - 按新聞來源分組，分析不同類型文章的區別度差異

使用方式：
    python -m indexing.poc_source_analysis
"""
import json
import sys
from pathlib import Path
from collections import defaultdict
from typing import List, Dict
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from indexing.poc_length_analysis import LengthBasedChunker, analyze_length
from indexing.poc_chunking import parse_tsv_line


def extract_source(url: str) -> str:
    """從 URL 提取來源"""
    if 'udn.com' in url:
        return 'UDN 聯合'
    elif 'ltn.com' in url:
        return 'LTN 自由'
    elif 'cna.com' in url:
        return 'CNA 中央社'
    elif 'ettoday' in url:
        return 'ETtoday'
    elif 'chinatimes' in url:
        return '中時'
    else:
        return '其他'


def load_articles_from_multiple_tsv(tsv_files: List[str], max_per_file: int = 100) -> List[Dict]:
    """從多個 TSV 載入文章"""
    articles = []

    for tsv_path in tsv_files:
        if not Path(tsv_path).exists():
            print(f"跳過（不存在）: {tsv_path}")
            continue

        print(f"讀取: {tsv_path}")
        count = 0

        with open(tsv_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        start = 1 if lines and lines[0].strip().startswith('url\t') else 0

        for line in lines[start:]:
            if count >= max_per_file:
                break
            try:
                url, json_data = parse_tsv_line(line)
                body = json_data.get('articleBody', '')
                if body and len(body) >= 100:
                    json_data['_url'] = url
                    json_data['_source'] = extract_source(url)
                    json_data['_length'] = len(body)
                    articles.append(json_data)
                    count += 1
            except:
                continue

        print(f"  載入 {count} 篇")

    return articles


def analyze_by_source(articles: List[Dict], target_length: int = 200):
    """按來源分組分析"""

    # 按來源分組
    by_source = defaultdict(list)
    for article in articles:
        by_source[article['_source']].append(article)

    # 按長度分組
    by_length = {
        '短文 (<500字)': [a for a in articles if a['_length'] < 500],
        '中等 (500-1000字)': [a for a in articles if 500 <= a['_length'] < 1000],
        '長文 (>1000字)': [a for a in articles if a['_length'] >= 1000]
    }

    chunker = LengthBasedChunker()
    results = {}

    print(f"\n{'='*70}")
    print(f"按來源分析（目標長度: {target_length} 字）")
    print(f"{'='*70}")
    print(f"{'來源':<15} {'文章數':<8} {'chunks/篇':<10} {'區別度':<15} {'標準差':<10}")
    print("-" * 70)

    for source, source_articles in sorted(by_source.items()):
        if len(source_articles) < 3:
            continue

        all_between_sims = []
        total_chunks = 0

        for article in source_articles:
            body = article.get('articleBody', '')
            chunks = chunker.chunk_by_length(body, target_length=target_length)
            total_chunks += len(chunks)

            between_sims = chunker.compute_between_chunk_similarity(chunks)
            all_between_sims.extend(between_sims)

        avg_sim = np.mean(all_between_sims) if all_between_sims else 0
        std_sim = np.std(all_between_sims) if all_between_sims else 0
        avg_chunks = total_chunks / len(source_articles)

        results[source] = {
            'count': len(source_articles),
            'avg_chunks': avg_chunks,
            'between_similarity_avg': avg_sim,
            'between_similarity_std': std_sim,
            'all_similarities': all_between_sims
        }

        print(f"{source:<15} {len(source_articles):<8} {avg_chunks:<10.1f} {avg_sim:<15.3f} {std_sim:<10.3f}")

    print(f"\n{'='*70}")
    print(f"按文章長度分析（目標 chunk: {target_length} 字）")
    print(f"{'='*70}")
    print(f"{'長度類型':<20} {'文章數':<8} {'chunks/篇':<10} {'區別度':<15} {'標準差':<10}")
    print("-" * 70)

    for length_type, length_articles in by_length.items():
        if len(length_articles) < 3:
            print(f"{length_type:<20} {len(length_articles):<8} (樣本太少)")
            continue

        all_between_sims = []
        total_chunks = 0

        for article in length_articles:
            body = article.get('articleBody', '')
            chunks = chunker.chunk_by_length(body, target_length=target_length)
            total_chunks += len(chunks)

            between_sims = chunker.compute_between_chunk_similarity(chunks)
            all_between_sims.extend(between_sims)

        avg_sim = np.mean(all_between_sims) if all_between_sims else 0
        std_sim = np.std(all_between_sims) if all_between_sims else 0
        avg_chunks = total_chunks / len(length_articles)

        results[length_type] = {
            'count': len(length_articles),
            'avg_chunks': avg_chunks,
            'between_similarity_avg': avg_sim,
            'between_similarity_std': std_sim
        }

        print(f"{length_type:<20} {len(length_articles):<8} {avg_chunks:<10.1f} {avg_sim:<15.3f} {std_sim:<10.3f}")

    return results


def main():
    project_root = Path(__file__).parent.parent.parent.parent

    # 使用更多 TSV 檔案
    tsv_files = [
        project_root / "crawled" / "已上傳" / "cna_2025_12.tsv",
        project_root / "crawled" / "已上傳" / "ltn_2025_12.tsv",
        project_root / "crawled" / "udn_2025_12.tsv",
    ]

    # 載入更多文章
    articles = load_articles_from_multiple_tsv(
        [str(f) for f in tsv_files],
        max_per_file=50  # 每個來源 50 篇
    )

    print(f"\n總共載入: {len(articles)} 篇文章")

    # 分析
    results = analyze_by_source(articles, target_length=200)

    # 總結
    print(f"\n{'='*70}")
    print("結論")
    print(f"{'='*70}")

    # 計算所有文章的整體區別度
    chunker = LengthBasedChunker()
    all_sims = []
    for article in articles:
        body = article.get('articleBody', '')
        chunks = chunker.chunk_by_length(body, target_length=200)
        all_sims.extend(chunker.compute_between_chunk_similarity(chunks))

    overall_avg = np.mean(all_sims)
    overall_std = np.std(all_sims)

    print(f"\n整體區別度: {overall_avg:.3f} ± {overall_std:.3f}")
    print(f"範圍: {np.min(all_sims):.3f} ~ {np.max(all_sims):.3f}")

    # 分位數
    percentiles = [10, 25, 50, 75, 90]
    print(f"\n分位數分布:")
    for p in percentiles:
        val = np.percentile(all_sims, p)
        print(f"  {p}%: {val:.3f}")

    # 儲存結果
    import os
    output_path = project_root / "code" / "python" / "data" / "indexing" / "poc_source_analysis.json"
    os.makedirs(output_path.parent, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        # 移除 numpy arrays（不能 JSON 序列化）
        save_results = {}
        for k, v in results.items():
            save_results[k] = {kk: vv for kk, vv in v.items() if kk != 'all_similarities'}
        json.dump({
            'results_by_group': save_results,
            'overall': {
                'avg': overall_avg,
                'std': overall_std,
                'min': float(np.min(all_sims)),
                'max': float(np.max(all_sims)),
                'percentiles': {str(p): float(np.percentile(all_sims, p)) for p in percentiles}
            }
        }, f, ensure_ascii=False, indent=2)

    print(f"\n結果已儲存到: {output_path}")

    # 釋放模型
    from indexing.poc_chunking import POCChunkingEngine
    POCChunkingEngine.unload_model()


if __name__ == "__main__":
    main()
