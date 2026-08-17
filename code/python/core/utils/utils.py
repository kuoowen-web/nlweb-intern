
import logging
from core.config import CONFIG

logger = logging.getLogger(__name__)

recipe_sites = ['seriouseats', 'hebbarskitchen', 'latam_recipes',
                'woksoflife', 'cheftariq',  'spruce', 'nytimes']

all_sites = recipe_sites + ["imdb", "npr podcasts", "neurips", "backcountry", "tripadvisor", "DataCommons"]

def siteToItemType(site):
    # Get item type from configuration
    namespace = "http://nlweb.ai/base"
    
    # Try to get from configuration
    try:
        site_config = CONFIG.get_site_config(site.lower())
        if site_config and site_config.item_types:
            # Return the first item type for the site
            return f"{{{namespace}}}{site_config.item_types[0]}"
    except Exception as e:
        logger.debug(f"Site config lookup failed for {site}: {e}")
    
    # Default to Item if not found in configuration
    return f"{{{namespace}}}Item"

    

def itemTypeToSite(item_type):
    # this is used to route queries that this site cannot answer,
    # but some other site can answer. Needs to be generalized.
    sites = []
    for site in all_sites:
        if siteToItemType(site) == item_type:
            sites.append(site)
    return sites
   
def visibleUrlLink(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return f"<a href='{url}'>{parsed.netloc.replace('www.', '')}</a>"

def visibleUrl(url):
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.replace('www.', '')

def get_param(query_params, param_name, param_type=str, default_value=None):
    value = query_params.get(param_name, default_value)
    if (value is not None):
        if param_type == str:
            if isinstance(value, list):
                return value[0] if value else ""
            return value
        elif param_type == int:
            return int(value)
        elif param_type == float:
            return float(value) 
        elif param_type == bool:
            if isinstance(value, list):
                return value[0].lower() == "true"
            return value.lower() == "true"
        elif param_type == list:
            if isinstance(value, list):
                return value
            # Try JSON parsing first for proper array handling
            try:
                import json
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            # Fallback to simple comma-split
            return [item.strip() for item in value.strip('[]').split(',') if item.strip()]
        else:
            raise ValueError(f"Unsupported parameter type: {param_type}")
    return default_value

# 票 2026-08-14-f F3：改用專案的 configured logger。
# 舊寫法 `logging.getLogger("nlweb.debug")` 全 repo 無配置 ⇒ 走
# logging.lastResort 到裸 stderr（無 timestamp / logger / level 欄位），
# 與專案其餘 JSON 日誌不同源。而 parse_bool_param 的 WARNING 是
# 「未知值被靜默判成關閉」這條風險的唯一可觀測性 —— 尤其對 streaming
# （誤關 ⇒ SSE 整條消失且靜默），落在 lastResort 等於實質不可見。
#
# ⚠ get_configured_logger 回傳 LazyLogger（misc/logger/logging_config_helper.py:414），
#   **不是** stdlib logging.Logger：有 .module_name、沒有 .name，且
#   .warning() 是非同步 enqueue 到背景 worker thread。要在測試裡觀測，
#   必須掛 handler 到底層 logging.getLogger("core_utils")（propagate=False）
#   並 poll 等 flush —— 範本見
#   tests/unit/core/test_bool_flag_whitelist.py::test_warning_reaches_project_log_handler。
from misc.logger.logging_config_helper import get_configured_logger

_utils_logger = get_configured_logger("core_utils")


# ── 旗標真值判定的唯一住所（票 2026-08-14-f）────────────────────
# 白名單式：只有明確的真值才開啟，其餘（含 JSON boolean false、空字串、
# 'no'/'off'/'FALSE'、int 0、看不懂的值）一律關閉。
#
# ⚠ 為什麼不是黑名單：黑名單列舉的是「假值的實例」，而輸入空間是開放的
#   —— 實測 'FALSE'、int 0、'no'、'off'、''、空 list 全都不在任何一版黑名單裡，
#   全被判成「開啟」。安全開關的正確預設方向是「未知即關」。
#
# ⚠ 與票 2026-08-14-c R4「邊界檢查要黑名單式」不矛盾：那條講**涵蓋面檢查**
#   （列舉哪些型態不被處理，目的是窮舉漏網）；本條講**安全開關**（判斷要不要
#   開，目的是安全預設）。判準：涵蓋面檢查用黑名單找漏，安全開關用白名單防誤開。
#
# ⚠ 不要在別處自己解析旗標真值（不管用 `not in [...]`、`bool()`、`!=`、
#   還是任何其他寫法）：tests/unit/core/test_bool_flag_whitelist_gate.py
#   的資料流掃描會命中「值來自 get_param(布林旗標) 卻不是呼叫本函式」的賦值。
_TRUE_TOKENS = frozenset({"true", "1", "yes", "on"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "off", ""})


def parse_bool_param(query_params, param_name, default: bool) -> bool:
    """把 query param 解析成 bool。白名單式：只有明確真值才回 True。

    Args:
        query_params: 請求參數 dict（值可能是 str / bool / int / list —— JSON
            body 經 `query_params.update(body_data)` 進來時保留原生型別）。
        param_name: 參數名。
        default: **鍵不存在或值為 None** 時的結果。必須是 Python bool，
            讓「預設開/關」在 callsite 一眼可讀。
            ⚠ 空 list `[]` **不走 default**：對齊 get_param 的
            `value[0] if value else ""` ⇒ 視同空字串 ⇒ False。
            「送了鍵但沒值」與「沒送鍵」語義不同，前者走「未知即關」。

    Returns:
        bool。無法辨識的值（'maybe'、int 7、dict…）回 False 並發 WARNING
        （未知即關 + 吵一聲）。**明確的真/假值不發 WARNING**——它們不是
        「看不懂」，對它們發警告會在 chat.js 送 false 時刷出假警報。

    ⚠ list 展開不是可選的實作細節：本 repo 的 query_params 有兩種型別並存
      （from_message / parse_qs 路徑是 Dict[str, List[str]]，
       dict(request.query) 路徑是 Dict[str, str]），本函式取代 get_param
      成為旗標取值入口，就繼承了 get_param 的 list 展開責任。拔掉它，
      core/baseHandler.py:397（2026-08-16 當下，AST 實測；舊註解寫 :387 已過期）
      注入的 `query_params["streaming"] = ["true"]` 會判關 ⇒ 該路徑 SSE 靜默消失。
    """
    if param_name not in query_params:
        return default

    value = query_params[param_name]

    if isinstance(value, list):
        # 對齊 get_param(..., str, ...)：取首，空 list 視同空字串
        value = value[0] if value else ""

    if value is None:
        return default

    # ⚠ bool 必須在 int 之前判：Python 的 bool 是 int 的子類
    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        # S-2：未知 int 與未知字串同級，都要吵
        _utils_logger.warning(
            "parse_bool_param: 無法辨識的旗標值，已判定為關閉（未知即關）。"
            f" param={param_name!r} value={value!r}"
        )
        return False

    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        _utils_logger.warning(
            "parse_bool_param: 無法辨識的旗標值，已判定為關閉（未知即關）。"
            f" param={param_name!r} value={value!r}"
        )
        return False

    _utils_logger.warning(
        "parse_bool_param: 非預期型別的旗標值，已判定為關閉（未知即關）。"
        f" param={param_name!r} type={type(value).__name__} value={value!r}"
    )
    return False


def log(message):
    _utils_logger.debug(message)