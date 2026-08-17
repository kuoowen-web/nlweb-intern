"""PostgreSQL 連線失敗訊息的單一來源。

原本 auth_db / analytics_db / user_data_db / postgres_client 四個模組各自
hardcode「是不是忘記開 Docker Desktop？」，且**無條件**這樣建議。當連線目標
不是 localhost 時這句是誤導 —— 實際遇過的 case 是外部開發者的 .env 仍是
`.env.example` 的字面佔位值（host / db / user 三個都是佔位字），DNS 解析
不到，跟 Docker 有沒有開完全無關。

本模組把「依 host 分流訊息」的邏輯收成一份：
  - host 是 localhost / 127.0.0.1 / ::1 → 保留 Docker Desktop 建議
  - 其他 host → 指向 .env 的 POSTGRES_CONNECTION_STRING 佔位值

兩個入口對應呼叫端已有的兩種資料形狀：
  - `pg_connect_error_message(database_url)` — 手上是完整連線字串
  - `pg_connect_error_message_for_host(host, port)` — 手上已是拆好的 host/port
"""

from typing import Optional
from urllib.parse import urlparse

# 視為「本機 Docker container」的 host。命中才建議去開 Docker Desktop。
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"})


def _extract_host(database_url: str) -> Optional[str]:
    """從連線字串取出 host，取不到回 None（呼叫端退回泛用訊息）。

    容忍兩種寫法：
      - `postgresql://user:pw@host:5432/db`（host 在 netloc）
      - `postgresql://host:5432/db?user=user`（無 credentials，host 仍在 netloc）
    """
    try:
        parsed = urlparse(database_url)
    except Exception:
        return None
    hostname = parsed.hostname
    if hostname:
        return hostname
    # 非 URL 形式（例如 libpq keyword/value）→ 交回 None，不強猜
    return None


def _mask(database_url: str) -> str:
    """遮蔽連線字串裡的密碼，只留 host 之後的部分（沿用原本各模組的做法）。"""
    return database_url.split("@")[1] if "@" in database_url else database_url


def pg_connect_error_message_for_host(host: Optional[str], port: Optional[object] = None) -> str:
    """依 host 產出連線失敗訊息。host 為 None / 空字串時回泛用訊息。"""
    target = f"{host}:{port}" if host and port else (host or "未知位址")

    if not host:
        return (
            f"無法連線到 PostgreSQL ({target})。"
            f"連線字串裡找不到 host —— 請檢查 .env 的 POSTGRES_CONNECTION_STRING 格式。"
        )

    if host.lower() in _LOCAL_HOSTS:
        return (
            f"無法連線到 PostgreSQL ({target})。"
            f"是不是忘記開 Docker Desktop？（或本機 PG container 尚未啟動：bash scripts/dev-up.sh）"
        )

    return (
        f"無法連線到 PostgreSQL ({target})。"
        f"連線字串的 host `{host}` 連不上／解析不到 —— "
        f"請檢查 .env 的 POSTGRES_CONNECTION_STRING 是否仍是 .env.example 的佔位值，"
        f"本機開發應指向 localhost:5432。"
    )


def pg_connect_error_message(database_url: str) -> str:
    """依連線字串產出連線失敗訊息（含遮蔽後的目標位址）。"""
    host = _extract_host(database_url)
    masked = _mask(database_url)

    if not host:
        return (
            f"無法連線到 PostgreSQL ({masked})。"
            f"連線字串裡找不到 host —— 請檢查 .env 的 POSTGRES_CONNECTION_STRING 格式。"
        )

    if host.lower() in _LOCAL_HOSTS:
        return (
            f"無法連線到 PostgreSQL ({masked})。"
            f"是不是忘記開 Docker Desktop？（或本機 PG container 尚未啟動：bash scripts/dev-up.sh）"
        )

    return (
        f"無法連線到 PostgreSQL ({masked})。"
        f"連線字串的 host `{host}` 連不上／解析不到 —— "
        f"請檢查 .env 的 POSTGRES_CONNECTION_STRING 是否仍是 .env.example 的佔位值，"
        f"本機開發應指向 localhost:5432。"
    )
