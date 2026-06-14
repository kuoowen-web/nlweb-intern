"""
POC Semantic Chunking Engine for M0 Indexing Module

測試不同閾值（0.75-0.90）的語義分塊效果，驗證最佳閾值後再正式實作。

使用方式：
    python -m indexing.poc_chunking --test  # 手動測試單篇文章
    python -m indexing.poc_runner crawled/已上傳/test_5_articles.tsv  # 批次測試
"""
import re
import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np


@dataclass
class Chunk:
    """語義分塊結果"""
    chunk_index: int
    sentences: List[str]
    full_text: str
    char_start: int
    char_end: int

    @property
    def sentence_count(self) -> int:
        return len(self.sentences)

    @property
    def char_count(self) -> int:
        return len(self.full_text)


@dataclass
class ChunkingResult:
    """單篇文章的分塊結果"""
    url: str
    headline: str
    original_length: int
    threshold: float
    chunks: List[Chunk]
    sentence_count: int
    similarity_scores: List[float] = field(default_factory=list)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def avg_chunk_size(self) -> float:
        if not self.chunks:
            return 0
        return sum(c.char_count for c in self.chunks) / len(self.chunks)


class POCChunkingEngine:
    """
    POC 語義分塊引擎

    核心演算法：
    1. 將文章切成句子（使用中文標點符號）
    2. 為每個句子產生 embedding（使用本地模型）
    3. 計算相鄰句子的 cosine similarity
    4. 當 similarity < threshold 時切分
    """

    _model = None
    _model_loaded = False

    def __init__(self, threshold: float = 0.80):
        self.threshold = threshold

    @classmethod
    def get_model(cls):
        """Lazy loading - 只在需要時載入模型"""
        if not cls._model_loaded:
            print("Loading sentence-transformers model (first time may download ~420MB)...")
            from sentence_transformers import SentenceTransformer
            cls._model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            cls._model_loaded = True
            print("Model loaded successfully!")
        return cls._model

    @classmethod
    def unload_model(cls):
        """主動釋放記憶體"""
        if cls._model is not None:
            del cls._model
            cls._model = None
            cls._model_loaded = False
            import gc
            gc.collect()
            print("Model unloaded.")

    def split_sentences(self, text: str) -> List[str]:
        """
        中文分句

        使用中文標點符號（。！？）作為分句依據，
        同時處理引號內的句子。
        """
        if not text:
            return []

        # 清理文字：移除多餘空白
        text = re.sub(r'\s+', ' ', text.strip())

        # 中文分句：以句號、問號、驚嘆號分割
        # 保留分隔符在前一句末尾
        pattern = r'([。！？!?])'
        parts = re.split(pattern, text)

        sentences = []
        current = ""

        for part in parts:
            if re.match(pattern, part):
                # 這是分隔符，附加到當前句子
                current += part
                if current.strip():
                    sentences.append(current.strip())
                current = ""
            else:
                current += part

        # 處理最後一個沒有標點的片段
        if current.strip():
            sentences.append(current.strip())

        # 過濾太短的句子（少於 5 個字元可能是雜訊）
        sentences = [s for s in sentences if len(s) >= 5]

        return sentences

    def compute_embeddings(self, sentences: List[str]) -> np.ndarray:
        """計算句子 embeddings"""
        if not sentences:
            return np.array([])

        model = self.get_model()
        embeddings = model.encode(sentences, show_progress_bar=False)
        return embeddings

    def compute_similarity(self, embeddings: np.ndarray) -> List[float]:
        """
        計算相鄰句子的 cosine similarity

        Returns:
            List of similarity scores between consecutive sentences.
            Length = len(embeddings) - 1
        """
        if len(embeddings) < 2:
            return []

        similarities = []
        for i in range(len(embeddings) - 1):
            # Cosine similarity
            a = embeddings[i]
            b = embeddings[i + 1]
            sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
            similarities.append(float(sim))

        return similarities

    def chunk_by_threshold(
        self,
        sentences: List[str],
        similarities: List[float],
        threshold: float
    ) -> List[Chunk]:
        """
        根據閾值分塊

        當相鄰句子的相似度 < threshold 時，在該處切分。
        """
        if not sentences:
            return []

        if len(sentences) == 1:
            return [Chunk(
                chunk_index=0,
                sentences=sentences,
                full_text=sentences[0],
                char_start=0,
                char_end=len(sentences[0])
            )]

        chunks = []
        current_sentences = [sentences[0]]
        current_start = 0
        char_pos = 0

        for i, sim in enumerate(similarities):
            next_sentence = sentences[i + 1]

            if sim < threshold:
                # 相似度低於閾值，切分
                chunk_text = ''.join(current_sentences)
                chunks.append(Chunk(
                    chunk_index=len(chunks),
                    sentences=current_sentences.copy(),
                    full_text=chunk_text,
                    char_start=char_pos,
                    char_end=char_pos + len(chunk_text)
                ))
                char_pos += len(chunk_text)
                current_sentences = [next_sentence]
            else:
                # 相似度高於閾值，繼續累積
                current_sentences.append(next_sentence)

        # 處理最後一個 chunk
        if current_sentences:
            chunk_text = ''.join(current_sentences)
            chunks.append(Chunk(
                chunk_index=len(chunks),
                sentences=current_sentences,
                full_text=chunk_text,
                char_start=char_pos,
                char_end=char_pos + len(chunk_text)
            ))

        return chunks

    def chunk_article(
        self,
        article_body: str,
        threshold: Optional[float] = None,
        url: str = "",
        headline: str = ""
    ) -> ChunkingResult:
        """
        對單篇文章進行語義分塊

        Args:
            article_body: 文章內容
            threshold: 分塊閾值（預設使用 self.threshold）
            url: 文章 URL
            headline: 文章標題

        Returns:
            ChunkingResult 包含分塊結果與統計資訊
        """
        if threshold is None:
            threshold = self.threshold

        # 1. 分句
        sentences = self.split_sentences(article_body)

        if not sentences:
            return ChunkingResult(
                url=url,
                headline=headline,
                original_length=len(article_body),
                threshold=threshold,
                chunks=[],
                sentence_count=0
            )

        # 2. 計算 embeddings
        embeddings = self.compute_embeddings(sentences)

        # 3. 計算相鄰句子相似度
        similarities = self.compute_similarity(embeddings)

        # 4. 根據閾值分塊
        chunks = self.chunk_by_threshold(sentences, similarities, threshold)

        return ChunkingResult(
            url=url,
            headline=headline,
            original_length=len(article_body),
            threshold=threshold,
            chunks=chunks,
            sentence_count=len(sentences),
            similarity_scores=similarities
        )


def parse_tsv_line(line: str) -> Tuple[str, dict]:
    """解析 TSV 行"""
    parts = line.strip().split('\t', 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid TSV line: {line[:50]}...")

    url = parts[0]
    json_data = json.loads(parts[1])
    return url, json_data


def print_chunking_result(result: ChunkingResult, verbose: bool = True):
    """格式化輸出分塊結果"""
    print(f"\n{'='*60}")
    print(f"URL: {result.url}")
    print(f"標題: {result.headline}")
    print(f"原文長度: {result.original_length} 字元")
    print(f"句子數: {result.sentence_count}")
    print(f"閾值: {result.threshold}")
    print(f"分塊數: {result.chunk_count}")
    print(f"平均 chunk 大小: {result.avg_chunk_size:.0f} 字元")
    print(f"{'='*60}")

    if verbose and result.chunks:
        for i, chunk in enumerate(result.chunks):
            print(f"\n--- Chunk {i+1} ({chunk.sentence_count} 句, {chunk.char_count} 字) ---")
            # 顯示前 200 字
            preview = chunk.full_text[:200]
            if len(chunk.full_text) > 200:
                preview += "..."
            print(preview)

    if result.similarity_scores:
        print(f"\n相似度分布: min={min(result.similarity_scores):.3f}, "
              f"max={max(result.similarity_scores):.3f}, "
              f"avg={np.mean(result.similarity_scores):.3f}")


def test_single_article():
    """手動測試單篇文章"""
    # 測試文章
    test_article = """
    鳳凰颱風直撲台灣，高雄市政府今天召開應變會議，市長陳其邁在會後表示，明天是否停班課，要看晚間7時氣象署的最新預報資料。

    陳其邁表示，目前颱風中心在鵝鑾鼻的西南方約350公里左右，預計明天早上6點暴風圈會觸及陸地，風力影響最強的時間，從明天早上6點到下午6點。

    在雨量的影響部分，陳其邁說，在山區大概是200毫米到400毫米左右。他今天下午與山區區長做視訊會議，特別提醒注意土土石流警戒，如果出現黃色警戒發布，必須採取預防性撤離；包括孕婦和慢性病患等，累計達到62人，將隨時留意撤離。

    陳其邁表示，鳳凰颱風雖然從中度颱風轉為輕度，不能掉以輕心，颱風的中心點登陸，如果是在高雄的北邊，對濱海地區影響較大；如果在高雄的南邊屏東登陸，對高雄影響比較小，請民眾需隨時留意颱風的動態，做好防颱措施。
    """

    engine = POCChunkingEngine()

    # 測試不同閾值
    thresholds = [0.75, 0.80, 0.85, 0.90]

    print("="*60)
    print("POC 語義分塊測試 - 單篇文章")
    print("="*60)

    for threshold in thresholds:
        result = engine.chunk_article(
            article_body=test_article,
            threshold=threshold,
            url="test://example",
            headline="鳳凰颱風明晨登陸高雄是否停班課"
        )
        print_chunking_result(result, verbose=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_single_article()
    else:
        print("使用方式:")
        print("  python -m indexing.poc_chunking --test  # 手動測試單篇文章")
        print("  python -m indexing.poc_runner <tsv_file>  # 批次測試")
