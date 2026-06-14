"""
POC 長度分析 - 以目標 chunk 長度為基準，分析 cosine similarity 分布

目的：
1. 按固定長度切分（200, 300, 400, 500 字）
2. 計算每個 chunk 內部句子的相似度（內聚度）
3. 計算相鄰 chunk 之間的相似度（區別度）
4. 找出最佳 chunk 長度

使用方式：
    python -m indexing.poc_length_analysis "../../crawled/poc_test_20.tsv"
"""
import json
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from indexing.poc_chunking import POCChunkingEngine, parse_tsv_line


@dataclass
class ChunkStats:
    """單個 chunk 的統計"""
    char_count: int
    sentence_count: int
    internal_similarity_avg: Optional[float]  # chunk 內句子平均相似度（內聚度）
    internal_similarity_min: Optional[float]
    internal_similarity_max: Optional[float]


@dataclass
class LengthAnalysisResult:
    """單一目標長度的分析結果"""
    target_length: int
    total_articles: int
    total_chunks: int
    avg_chunks_per_article: float
    avg_actual_chunk_length: float

    # 內聚度：chunk 內部句子的相似度（越高越好）
    internal_cohesion_avg: float
    internal_cohesion_std: float

    # 區別度：相鄰 chunk 之間的相似度（越低越好）
    between_chunk_similarity_avg: float
    between_chunk_similarity_std: float

    # Chunk 邊界處的相似度分布
    boundary_similarities: List[float]


class LengthBasedChunker:
    """基於長度的分塊器"""

    def __init__(self):
        self.engine = POCChunkingEngine()

    def chunk_by_length(
        self,
        text: str,
        target_length: int = 300,
        min_length: int = 100
    ) -> List[Dict]:
        """
        按目標長度切分文章

        Args:
            text: 文章內容
            target_length: 目標 chunk 長度
            min_length: 最小 chunk 長度（最後一塊如果太短會合併到前一塊）

        Returns:
            List of chunks, each with sentences and embeddings
        """
        # 先分句
        sentences = self.engine.split_sentences(text)
        if not sentences:
            return []

        # 計算 embeddings
        embeddings = self.engine.compute_embeddings(sentences)

        # 按長度累積切分
        chunks = []
        current_sentences = []
        current_embeddings = []
        current_length = 0

        for i, sentence in enumerate(sentences):
            sent_len = len(sentence)

            # 如果加入這句會超過目標長度，且當前已有內容
            if current_length + sent_len > target_length and current_sentences:
                # 儲存當前 chunk
                chunks.append({
                    'sentences': current_sentences.copy(),
                    'embeddings': np.array(current_embeddings),
                    'text': ''.join(current_sentences),
                    'char_count': current_length
                })
                current_sentences = []
                current_embeddings = []
                current_length = 0

            current_sentences.append(sentence)
            current_embeddings.append(embeddings[i])
            current_length += sent_len

        # 處理最後一個 chunk
        if current_sentences:
            # 如果最後一塊太短，合併到前一塊
            if current_length < min_length and chunks:
                last_chunk = chunks[-1]
                last_chunk['sentences'].extend(current_sentences)
                last_chunk['embeddings'] = np.vstack([last_chunk['embeddings'], current_embeddings])
                last_chunk['text'] += ''.join(current_sentences)
                last_chunk['char_count'] += current_length
            else:
                chunks.append({
                    'sentences': current_sentences,
                    'embeddings': np.array(current_embeddings),
                    'text': ''.join(current_sentences),
                    'char_count': current_length
                })

        return chunks

    def compute_internal_similarity(self, embeddings: np.ndarray) -> Dict:
        """計算 chunk 內部句子的相似度（內聚度）"""
        if len(embeddings) < 2:
            return {'avg': None, 'min': None, 'max': None}

        similarities = []
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                sim = np.dot(embeddings[i], embeddings[j]) / (
                    np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                )
                similarities.append(float(sim))

        return {
            'avg': np.mean(similarities),
            'min': np.min(similarities),
            'max': np.max(similarities),
            'all': similarities
        }

    def compute_chunk_embedding(self, embeddings: np.ndarray) -> np.ndarray:
        """計算 chunk 的整體 embedding（取平均）"""
        return np.mean(embeddings, axis=0)

    def compute_between_chunk_similarity(self, chunks: List[Dict]) -> List[float]:
        """計算相鄰 chunk 之間的相似度（區別度）"""
        if len(chunks) < 2:
            return []

        similarities = []
        for i in range(len(chunks) - 1):
            emb1 = self.compute_chunk_embedding(chunks[i]['embeddings'])
            emb2 = self.compute_chunk_embedding(chunks[i + 1]['embeddings'])
            sim = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
            similarities.append(float(sim))

        return similarities


def analyze_length(
    chunker: LengthBasedChunker,
    articles: List[Dict],
    target_length: int
) -> LengthAnalysisResult:
    """分析特定目標長度的效果"""

    all_internal_cohesions = []
    all_between_similarities = []
    all_chunk_lengths = []
    total_chunks = 0

    for article in articles:
        body = article.get('articleBody', '')
        if not body or len(body) < 100:
            continue

        chunks = chunker.chunk_by_length(body, target_length=target_length)
        total_chunks += len(chunks)

        # 收集每個 chunk 的內聚度
        for chunk in chunks:
            all_chunk_lengths.append(chunk['char_count'])
            internal = chunker.compute_internal_similarity(chunk['embeddings'])
            if internal['avg'] is not None:
                all_internal_cohesions.append(internal['avg'])

        # 收集相鄰 chunk 之間的相似度
        between_sims = chunker.compute_between_chunk_similarity(chunks)
        all_between_similarities.extend(between_sims)

    return LengthAnalysisResult(
        target_length=target_length,
        total_articles=len(articles),
        total_chunks=total_chunks,
        avg_chunks_per_article=total_chunks / len(articles) if articles else 0,
        avg_actual_chunk_length=np.mean(all_chunk_lengths) if all_chunk_lengths else 0,
        internal_cohesion_avg=np.mean(all_internal_cohesions) if all_internal_cohesions else 0,
        internal_cohesion_std=np.std(all_internal_cohesions) if all_internal_cohesions else 0,
        between_chunk_similarity_avg=np.mean(all_between_similarities) if all_between_similarities else 0,
        between_chunk_similarity_std=np.std(all_between_similarities) if all_between_similarities else 0,
        boundary_similarities=all_between_similarities
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description='POC 長度分析')
    parser.add_argument('tsv_file', help='TSV 檔案路徑')
    parser.add_argument('--lengths', '-l', nargs='+', type=int,
                        default=[150, 200, 300, 400, 500],
                        help='要測試的目標長度列表')
    parser.add_argument('--output', '-o', default='data/indexing/poc_length_analysis.json',
                        help='輸出 JSON 檔案路徑')

    args = parser.parse_args()

    # 讀取文章
    print(f"\n讀取 TSV 檔案: {args.tsv_file}")
    articles = []

    with open(args.tsv_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    start = 1 if lines and lines[0].strip().startswith('url\t') else 0
    for line in lines[start:]:
        try:
            url, json_data = parse_tsv_line(line)
            articles.append(json_data)
        except:
            continue

    print(f"載入 {len(articles)} 篇文章")

    # 初始化
    chunker = LengthBasedChunker()
    results = {}

    # 測試每個目標長度
    for target_length in args.lengths:
        print(f"\n分析目標長度: {target_length} 字...")
        result = analyze_length(chunker, articles, target_length)
        results[str(target_length)] = asdict(result)

        print(f"  - 平均 chunks/篇: {result.avg_chunks_per_article:.1f}")
        print(f"  - 實際平均長度: {result.avg_actual_chunk_length:.0f} 字")
        print(f"  - 內聚度 (越高越好): {result.internal_cohesion_avg:.3f} ± {result.internal_cohesion_std:.3f}")
        print(f"  - 區別度 (越低越好): {result.between_chunk_similarity_avg:.3f} ± {result.between_chunk_similarity_std:.3f}")

    # 輸出摘要表格
    print("\n" + "=" * 80)
    print("長度分析摘要")
    print("=" * 80)
    print(f"{'目標長度':<10} {'chunks/篇':<12} {'實際長度':<12} {'內聚度':<15} {'區別度':<15}")
    print("-" * 80)

    for length in args.lengths:
        r = results[str(length)]
        print(f"{length:<10} {r['avg_chunks_per_article']:<12.1f} "
              f"{r['avg_actual_chunk_length']:<12.0f} "
              f"{r['internal_cohesion_avg']:<15.3f} "
              f"{r['between_chunk_similarity_avg']:<15.3f}")

    print("\n說明:")
    print("  - 內聚度 (Internal Cohesion): chunk 內句子的平均相似度，越高表示語意越統一")
    print("  - 區別度 (Between-chunk Similarity): 相鄰 chunk 的相似度，越低表示切分越有意義")
    print("  - 理想: 內聚度高 + 區別度低")

    # 找出最佳長度
    best_length = None
    best_score = -float('inf')

    for length in args.lengths:
        r = results[str(length)]
        # 評分 = 內聚度 - 區別度（越高越好）
        score = r['internal_cohesion_avg'] - r['between_chunk_similarity_avg']
        if score > best_score:
            best_score = score
            best_length = length

    print(f"\n建議目標長度: {best_length} 字 (內聚度 - 區別度 = {best_score:.3f})")

    # 儲存結果
    import os
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n詳細結果已儲存到: {args.output}")

    # 釋放模型
    POCChunkingEngine.unload_model()


if __name__ == "__main__":
    main()
