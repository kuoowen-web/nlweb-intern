"""
POC Runner - 批次測試不同閾值的語義分塊效果

使用方式：
    python -m indexing.poc_runner crawled/已上傳/test_5_articles.tsv
    python -m indexing.poc_runner crawled/poc_test_20.tsv --output data/indexing/poc_results.json

輸出：
    - Console 摘要
    - JSON 檔案（供人工評估）
"""
import json
import sys
import os
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from datetime import datetime

# 確保可以 import indexing 模組
sys.path.insert(0, str(Path(__file__).parent.parent))

from indexing.poc_chunking import (
    POCChunkingEngine,
    ChunkingResult,
    parse_tsv_line,
    print_chunking_result
)


@dataclass
class ThresholdStats:
    """單一閾值的統計結果"""
    threshold: float
    total_articles: int
    total_chunks: int
    avg_chunks_per_article: float
    min_chunks: int
    max_chunks: int
    avg_chunk_size: float
    avg_sentences_per_chunk: float


@dataclass
class POCResults:
    """完整 POC 測試結果"""
    run_time: str
    tsv_file: str
    total_articles: int
    thresholds_tested: List[float]
    stats_by_threshold: Dict[str, ThresholdStats]
    articles: List[Dict[str, Any]]  # 每篇文章在各閾值下的分塊結果


def run_poc(
    tsv_path: str,
    thresholds: List[float] = None,
    max_articles: int = None,
    verbose: bool = False
) -> POCResults:
    """
    執行 POC 測試

    Args:
        tsv_path: TSV 檔案路徑
        thresholds: 要測試的閾值列表（預設 [0.75, 0.80, 0.85, 0.90]）
        max_articles: 最多測試幾篇（預設全部）
        verbose: 是否顯示詳細輸出

    Returns:
        POCResults 包含所有測試結果
    """
    if thresholds is None:
        thresholds = [0.75, 0.80, 0.85, 0.90]

    engine = POCChunkingEngine()

    # 讀取 TSV 檔案
    print(f"\n讀取 TSV 檔案: {tsv_path}")
    articles_data = []

    with open(tsv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 跳過 header
    if lines and lines[0].strip().startswith('url\t'):
        lines = lines[1:]

    for i, line in enumerate(lines):
        if max_articles and i >= max_articles:
            break

        try:
            url, json_data = parse_tsv_line(line)
            articles_data.append({
                'url': url,
                'headline': json_data.get('headline', ''),
                'articleBody': json_data.get('articleBody', '')
            })
        except Exception as e:
            print(f"警告: 無法解析第 {i+1} 行: {e}")

    print(f"共載入 {len(articles_data)} 篇文章")

    # 對每篇文章測試所有閾值
    all_results = []  # List of {url, headline, length, results_by_threshold}

    for i, article in enumerate(articles_data):
        print(f"\n處理第 {i+1}/{len(articles_data)} 篇: {article['headline'][:30]}...")

        article_result = {
            'url': article['url'],
            'headline': article['headline'],
            'original_length': len(article['articleBody']),
            'results_by_threshold': {}
        }

        for threshold in thresholds:
            result = engine.chunk_article(
                article_body=article['articleBody'],
                threshold=threshold,
                url=article['url'],
                headline=article['headline']
            )

            # 儲存結果
            article_result['results_by_threshold'][str(threshold)] = {
                'chunk_count': result.chunk_count,
                'sentence_count': result.sentence_count,
                'avg_chunk_size': result.avg_chunk_size,
                'chunks': [
                    {
                        'index': c.chunk_index,
                        'sentence_count': c.sentence_count,
                        'char_count': c.char_count,
                        'preview': c.full_text[:200] + ('...' if len(c.full_text) > 200 else '')
                    }
                    for c in result.chunks
                ],
                'similarity_stats': {
                    'min': min(result.similarity_scores) if result.similarity_scores else None,
                    'max': max(result.similarity_scores) if result.similarity_scores else None,
                    'avg': sum(result.similarity_scores) / len(result.similarity_scores) if result.similarity_scores else None
                }
            }

            if verbose:
                print_chunking_result(result, verbose=False)

        all_results.append(article_result)

    # 計算各閾值的統計
    stats_by_threshold = {}

    for threshold in thresholds:
        t_str = str(threshold)
        chunk_counts = [
            a['results_by_threshold'][t_str]['chunk_count']
            for a in all_results
        ]
        chunk_sizes = [
            a['results_by_threshold'][t_str]['avg_chunk_size']
            for a in all_results
            if a['results_by_threshold'][t_str]['avg_chunk_size'] > 0
        ]

        # 計算每個 chunk 的平均句子數
        all_chunks = []
        for a in all_results:
            all_chunks.extend(a['results_by_threshold'][t_str]['chunks'])

        avg_sentences = (
            sum(c['sentence_count'] for c in all_chunks) / len(all_chunks)
            if all_chunks else 0
        )

        stats_by_threshold[t_str] = ThresholdStats(
            threshold=threshold,
            total_articles=len(all_results),
            total_chunks=sum(chunk_counts),
            avg_chunks_per_article=sum(chunk_counts) / len(chunk_counts) if chunk_counts else 0,
            min_chunks=min(chunk_counts) if chunk_counts else 0,
            max_chunks=max(chunk_counts) if chunk_counts else 0,
            avg_chunk_size=sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0,
            avg_sentences_per_chunk=avg_sentences
        )

    return POCResults(
        run_time=datetime.now().isoformat(),
        tsv_file=tsv_path,
        total_articles=len(all_results),
        thresholds_tested=thresholds,
        stats_by_threshold={k: asdict(v) for k, v in stats_by_threshold.items()},
        articles=all_results
    )


def print_summary(results: POCResults):
    """輸出摘要報告"""
    print("\n" + "=" * 70)
    print("POC 測試結果摘要")
    print("=" * 70)
    print(f"測試時間: {results.run_time}")
    print(f"TSV 檔案: {results.tsv_file}")
    print(f"測試文章數: {results.total_articles}")
    print(f"測試閾值: {results.thresholds_tested}")
    print()

    # 表格輸出
    print(f"{'閾值':<10} {'平均chunks':<12} {'min':<6} {'max':<6} {'平均chunk大小':<14} {'平均句子數':<12}")
    print("-" * 70)

    for t_str, stats in results.stats_by_threshold.items():
        print(f"{stats['threshold']:<10.2f} "
              f"{stats['avg_chunks_per_article']:<12.1f} "
              f"{stats['min_chunks']:<6} "
              f"{stats['max_chunks']:<6} "
              f"{stats['avg_chunk_size']:<14.0f} "
              f"{stats['avg_sentences_per_chunk']:<12.1f}")

    print()
    print("建議:")
    print("- 目標: 平均 3-8 chunks/篇")
    print("- 閾值越高 → chunks 越多（切得越細）")
    print("- 閾值越低 → chunks 越少（切得越粗）")

    # 找出最接近目標的閾值
    target_avg = 5.5  # 目標 3-8 的中間值
    best_threshold = None
    best_diff = float('inf')

    for t_str, stats in results.stats_by_threshold.items():
        diff = abs(stats['avg_chunks_per_article'] - target_avg)
        if diff < best_diff:
            best_diff = diff
            best_threshold = stats['threshold']

    if best_threshold:
        print(f"\n根據平均 chunks 數，建議閾值: {best_threshold}")


def save_results(results: POCResults, output_path: str):
    """儲存結果到 JSON 檔案"""
    # 確保目錄存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(asdict(results), f, ensure_ascii=False, indent=2)

    print(f"\n結果已儲存到: {output_path}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='POC 語義分塊測試')
    parser.add_argument('tsv_file', help='TSV 檔案路徑')
    parser.add_argument('--output', '-o', default='data/indexing/poc_results.json',
                        help='輸出 JSON 檔案路徑')
    parser.add_argument('--max-articles', '-n', type=int, default=None,
                        help='最多測試幾篇文章')
    parser.add_argument('--thresholds', '-t', nargs='+', type=float,
                        default=[0.75, 0.80, 0.85, 0.90],
                        help='要測試的閾值列表')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='顯示詳細輸出')

    args = parser.parse_args()

    # 執行測試
    results = run_poc(
        tsv_path=args.tsv_file,
        thresholds=args.thresholds,
        max_articles=args.max_articles,
        verbose=args.verbose
    )

    # 輸出摘要
    print_summary(results)

    # 儲存結果
    save_results(results, args.output)

    # 釋放模型記憶體
    POCChunkingEngine.unload_model()


if __name__ == "__main__":
    main()
