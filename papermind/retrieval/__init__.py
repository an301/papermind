"""Retrieval over ingested papers: BM25, dense embeddings, and RRF fusion.

    registry  = IndexRegistry(Path("data/papers"))
    retriever = HybridRetriever(registry)
    await registry.warm()                       # build indexes at startup

    chunks = await retriever.search("what is scaled dot-product attention?",
                                    paper_id="1706.03762", k=5)

Three interchangeable paths, for ablation:

    await retriever.bm25_only(query, paper_id, k)
    await retriever.dense_only(query, paper_id, k)
    await retriever.hybrid(query, paper_id, k)

Indexes are scoped per paper -- a search only ever sees the paper being read.
"""

from papermind.retrieval.dense import DEFAULT_EMBEDDING_MODEL, EmbeddingModel
from papermind.retrieval.fusion import RRF_K, reciprocal_rank_fusion
from papermind.retrieval.hybrid import HybridRetriever, RetrievalMode
from papermind.retrieval.index import IndexRegistry, PaperIndex
from papermind.retrieval.models import ScoredChunk

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "RRF_K",
    "EmbeddingModel",
    "HybridRetriever",
    "IndexRegistry",
    "PaperIndex",
    "RetrievalMode",
    "ScoredChunk",
    "reciprocal_rank_fusion",
]
