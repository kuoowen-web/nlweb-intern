import pytest


def test_missing_backend_package_points_at_uv_extra(monkeypatch):
    from core import retriever

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("No module named 'psycopg'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    retriever._installed_packages.discard("psycopg")

    with pytest.raises(ImportError) as exc:
        retriever._ensure_package_installed("postgres")
    msg = str(exc.value)
    assert "postgres" in msg
    assert "uv sync --extra postgres" in msg
    assert "pip install" not in msg  # old wording must be gone
