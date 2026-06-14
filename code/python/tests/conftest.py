"""Pytest conftest for code/python/tests.

codex Imp-1 (Track A sprint 2026-05-28): LR strict invariants — test 環境必 fail-loud。
在 test 跑之前 set LR_STRICT_INVARIANTS=1，確保 schema invariant violation 在 CI 必 raise
（runtime 模式只 log warning + caller mark guard_failed，避免阻塞 user pipeline）。

亦 ensure code/python 在 sys.path（pytest 從不同 cwd 跑時必要）。
"""
import os
import sys

# Ensure `code/python` is on sys.path so `from reasoning...` imports work
# regardless of pytest invocation cwd.
_CODE_PYTHON = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _CODE_PYTHON not in sys.path:
    sys.path.insert(0, _CODE_PYTHON)


# ─────────────────────────────────────────────────────────────────────────────
# [interface: unit-llm-safe] LLM key 全域中和 — 防 unit/contract 套件意外打真 LLM。
#
# 機制：在任何 `core.config` import（其 import-time `load_dotenv(override=False)`
# 會從 .env 注入 key）之前，把所有 LLM provider 的 key env 設為空字串。
# load_dotenv(override=False) 不覆寫「已設定（含空字串）」的 var → key 恆空 →
# instructor client 與 legacy ask_llm 都拿不到 key → 乾淨 fail（log error/warning，
# 非 silent），絕不真打 LLM。
#
# opt-in 例外：contract tests 走 NLWEB_ALLOW_REAL_LLM=1 顯式放行（見
# tests/contract/test_agent_contracts.py 的 _api_key_available）。注意此 opt-in 是
# **整段 blank 全跳過**（見下方 if gate）—— 設了 NLWEB_ALLOW_REAL_LLM=1 後本中和不
# 執行，同 process 的 unit 套件也可能從 .env 載到真 key，**不保證 unit 隔離**。預設
# （未設此 var）才有「unit 套件拿不到 key」的保證。contract module 額外用
# load_dotenv(override=True) 重新注入真 key 給自己用。
#
# 涵蓋的 key = config/config_llm.yaml 所有 endpoint 的 api_key_env（實測列舉）。
# ─────────────────────────────────────────────────────────────────────────────
_LLM_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "NLWEB_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DEEPSEEK_AZURE_API_KEY",
    "GEMINI_API_KEY",
    "HF_TOKEN",
    "INCEPTION_API_KEY",
    "LLAMA_AZURE_API_KEY",
    "SNOWFLAKE_PAT",
)
if os.environ.get("NLWEB_ALLOW_REAL_LLM", "").strip() != "1":
    for _k in _LLM_KEY_ENV_VARS:
        os.environ[_k] = ""  # 已設定但空 → load_dotenv(override=False) 保留空


def pytest_configure(config):
    # codex Imp-1: LR strict invariants — test / CI 環境必 fail-loud
    os.environ.setdefault("LR_STRICT_INVARIANTS", "1")
