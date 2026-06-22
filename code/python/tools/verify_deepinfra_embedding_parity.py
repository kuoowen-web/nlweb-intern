"""Verify DeepInfra qwen3-embedding-4b is vector-space compatible with OpenRouter.

The DeepInfra fallback is only safe if its query embeddings land in the SAME
vector space as the OpenRouter primary (which is what PG's vector(1024) chunks
were built from). Same base model is necessary but NOT sufficient — different
inference backends / quantization / prompt handling can shift the vectors.

This script embeds the same queries via both providers and reports cosine
similarity. Threshold: >0.99 = safe to use as drop-in fallback.

Run (needs OPENROUTER_API_KEY + DEEPINFRA_API_KEY in .env):
    cd code/python && python tools/verify_deepinfra_embedding_parity.py
"""
import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"))

from embedding_providers.openrouter_embedding import get_openrouter_embedding
from embedding_providers.deepinfra_embedding import get_deepinfra_embedding

QUERIES = [
    "台灣基本工資調漲對中小企業的影響",
    "再生能源政策與農村發展",
    "minimum wage policy employment effects",
]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def main():
    print("Verifying OpenRouter vs DeepInfra qwen3-embedding-4b parity...\n")
    all_pass = True
    for q in QUERIES:
        try:
            o = await get_openrouter_embedding(q)
            d = await get_deepinfra_embedding(q)
        except Exception as e:
            print(f"  FAILED to embed '{q[:30]}...': {type(e).__name__}: {e}")
            all_pass = False
            continue
        if len(o) != len(d):
            print(f"  DIM MISMATCH '{q[:30]}...': OpenRouter={len(o)} DeepInfra={len(d)}")
            all_pass = False
            continue
        sim = cosine(o, d)
        ok = sim > 0.99
        all_pass = all_pass and ok
        print(f"  cosine={sim:.6f} dim={len(o)} {'PASS' if ok else 'FAIL (<0.99)'}  '{q[:40]}'")

    print()
    if all_pass:
        print("RESULT: PASS — DeepInfra is vector-space compatible, safe as fallback.")
    else:
        print("RESULT: FAIL — vectors diverge; DeepInfra fallback would degrade retrieval.")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
