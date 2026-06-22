"""Guardrail: retriever.py 不再有任何 qdrant 分支，但 PG 分支完整保留。"""
import inspect
from core import retriever


def test_no_qdrant_in_db_type_maps():
    # qdrant 鍵必須從 package/extras map 消失，postgres 必須保留
    assert "qdrant" not in retriever._db_type_packages
    assert "qdrant" not in retriever._db_type_extras
    assert "postgres" in retriever._db_type_packages
    assert "postgres" in retriever._db_type_extras


def test_no_qdrant_dispatch_branch_in_source():
    # 原始碼層級確認：retriever.py 不再 import QdrantVectorClient
    src = inspect.getsource(retriever)
    assert "QdrantVectorClient" not in src
    assert "retrieval_providers.qdrant" not in src
    # PG 分發仍在
    assert "PgVectorClient" in src
    assert "retrieval_providers.postgres_client" in src


def test_qdrant_client_import_check_removed():
    # _ensure_package_installed 不再有 qdrant_client 的特判
    src = inspect.getsource(retriever._ensure_package_installed)
    assert "qdrant_client" not in src
    assert "psycopg" in src  # PG 特判保留
