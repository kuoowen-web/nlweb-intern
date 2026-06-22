"""Guardrail: pipeline.py 不再 import qdrant_uploader，也無 qdrant upload 路徑。"""
import inspect
from indexing import pipeline


def test_no_qdrant_import_in_source():
    src = inspect.getsource(pipeline)
    assert "qdrant_uploader" not in src
    assert "QdrantUploader" not in src
    assert "QdrantConfig" not in src


def test_pipeline_init_has_no_qdrant_params():
    sig = inspect.signature(pipeline.IndexingPipeline.__init__)
    params = set(sig.parameters.keys())
    assert "upload_to_qdrant" not in params
    assert "qdrant_config" not in params


def test_no_qdrant_flush_or_reconcile_methods():
    assert not hasattr(pipeline.IndexingPipeline, "_flush_qdrant_buffer")
    assert not hasattr(pipeline.IndexingPipeline, "reconcile")
    # vault 路徑保留
    assert hasattr(pipeline.IndexingPipeline, "process_tsv")
    assert hasattr(pipeline.IndexingPipeline, "process_tsv_resumable")
