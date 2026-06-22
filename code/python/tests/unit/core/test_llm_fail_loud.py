import importlib
import pytest


def test_missing_optional_provider_fails_loud_with_extra_hint(monkeypatch):
    """When an optional provider's SDK is absent, loading it must raise a
    clear error naming the `uv sync --extra` to run — never silently pip-install."""
    from core import llm

    # Simulate the anthropic SDK being absent.
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    # Ensure not cached from a prior test.
    llm._loaded_providers.pop("anthropic", None)

    # F2 regression guard: the availability check runs INSIDE _get_provider's try,
    # so its ImportError is caught by the existing `except ImportError` and converted
    # to ValueError. This is what lets ask_llm's inner `except ValueError` classify it
    # as config_error (not provider_error). If the check were placed OUTSIDE the try,
    # _get_provider would raise ImportError here and this assertion would fail.
    with pytest.raises(ValueError) as exc:
        llm._get_provider("anthropic")
    msg = str(exc.value)
    assert "anthropic" in msg
    assert "uv sync --extra anthropic" in msg


def test_no_subprocess_pip_install_symbol_remains():
    """Guardrail: the subprocess auto-install helper must be gone."""
    from core import llm
    assert not hasattr(llm, "_ensure_package_installed"), \
        "_ensure_package_installed must be removed (no runtime pip install)"


class _FakeEndpoint:
    def __init__(self, llm_type):
        self.llm_type = llm_type


def _patch_endpoints(monkeypatch, endpoints, preferred):
    """Point CONFIG at a controlled set of endpoints for init() testing."""
    from core import llm
    monkeypatch.setattr(llm.CONFIG, "llm_endpoints", endpoints, raising=False)
    monkeypatch.setattr(llm.CONFIG, "preferred_llm_endpoint", preferred, raising=False)


def test_init_fails_hard_when_preferred_provider_sdk_missing(monkeypatch):
    """F8 = B (CEO): if the PREFERRED provider's SDK is absent, init() must
    re-raise (fail-hard at startup), not swallow into a warning."""
    from core import llm

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    llm._loaded_providers.pop("anthropic", None)

    _patch_endpoints(
        monkeypatch,
        {"anthropic": _FakeEndpoint("anthropic")},
        preferred="anthropic",
    )

    with pytest.raises(RuntimeError) as exc:
        llm.init()
    assert "anthropic" in str(exc.value)
    assert "uv sync --extra" in str(exc.value)


def test_init_does_not_fail_for_non_preferred_provider(monkeypatch):
    """A non-preferred endpoint whose SDK is missing must NOT take the service
    down — init() only fails-hard on the preferred endpoint."""
    from core import llm

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    llm._loaded_providers.pop("anthropic", None)
    llm._loaded_providers.pop("openai", None)

    # anthropic is present-but-broken, but it is NOT preferred. openai is preferred
    # and is a core dep, so init() must complete without raising.
    _patch_endpoints(
        monkeypatch,
        {
            "openai": _FakeEndpoint("openai"),
            "anthropic": _FakeEndpoint("anthropic"),
        },
        preferred="openai",
    )

    # Should not raise (only the preferred endpoint is acted upon).
    llm.init()
