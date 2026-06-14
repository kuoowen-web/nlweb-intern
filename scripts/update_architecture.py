#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_architecture.py (NLWeb 適配版)
功能：
- 從 architecture.html 中解析 JavaScript graphData 物件
- 使用 YAML 管理模組層級 (level 0=group, level 1=module)
- 自動檢測 Python 模組間的依賴關係 (import edges)
- 分析實現狀態 (done/partial/notdone)
- 輸出 graphData.json 供視覺化使用
"""

from __future__ import annotations
import os
import sys
import re
import ast
import json
import yaml
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Set, Optional

# ---------------------
# 配置區：根據你的專案結構修改
# ---------------------
# 注意：腳本位於 scripts/ 目錄，路徑需要相對於專案根目錄
SCRIPT_DIR = Path(__file__).parent  # scripts/
PROJECT_ROOT = SCRIPT_DIR.parent     # NLWeb/

HTML_FILE = str(PROJECT_ROOT / "static" / "architecture.html")
MODULE_ROOT = str(PROJECT_ROOT / "code" / "python")
YAML_PATH = str(PROJECT_ROOT / "architecture_levels.yaml")
GRAPH_JSON = str(PROJECT_ROOT / "graphData.json")
LAYOUT_JSON = str(PROJECT_ROOT / "architecture-diagram.json")  # 佈局備份

# ---------------------
# Fallback builtin level map (最後手段)
# ---------------------
FALLBACK_BUILTIN_MAP = {
    # Module headers (level 0)
    "mod-storage": 0, "mod-retrieval": 0, "mod-ranking": 0,
    "mod-search": 0, "mod-reasoning": 0, "mod-output": 0, "mod-others": 0,
    # Regular modules (level 1)
    "sys-db": 1, "sys-cache": 1, "internal-index": 1, "sys-vec": 1,
    "web-search-api": 1, "mmr": 1, "weight-calibrator": 1, "xgboost-ranker": 1,
}

STATUS_PATTERNS = ["TODO", "NotImplementedError", "pass"]

# ---------------------
# Helpers
# ---------------------
def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def load_yaml(path: str) -> Dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

def save_yaml(path: str, data: Dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

def ensure_yaml_levels_section(yaml_data: Dict):
    if "levels" not in yaml_data or not isinstance(yaml_data["levels"], dict):
        yaml_data["levels"] = {}

# ---------------------
# 解析 HTML 中的 JavaScript graphData
# ---------------------
def parse_graphdata_from_html(html_path: str) -> Tuple[List[Dict], List[Dict]]:
    """
    從 architecture.html 的 <script> 標籤中提取 graphData
    返回: (nodes, groups)
    """
    if not os.path.exists(html_path):
        print(f"[ERROR] {html_path} not found.")
        sys.exit(1)

    text = read_file(html_path)

    # 1. 提取 graphData = { ... }; 區塊
    match = re.search(r'const graphData\s*=\s*\{([\s\S]*?)\n\s*\};', text)
    if not match:
        print("[ERROR] Cannot find 'const graphData = {...};' in HTML")
        sys.exit(1)

    graphdata_content = match.group(1)

    # 2. 提取 nodes 陣列（支援 JavaScript 和 JSON 格式）
    # JavaScript: nodes: [...]
    # JSON: "nodes": [...]
    nodes_match = re.search(r'["\']?nodes["\']?\s*:\s*\[([\s\S]*?)\]\s*,\s*["\']?edges["\']?\s*:', graphdata_content)
    if not nodes_match:
        print("[ERROR] Cannot find 'nodes: [...]' or '\"nodes\": [...]' in graphData")
        sys.exit(1)

    nodes_str = nodes_match.group(1)

    # 3. 解析每個節點物件（支援 JavaScript 和 JSON 格式）
    # JavaScript: { id: 'sys-db', labelEn: 'Postgres DB', ... }
    # JSON: { "id": "sys-db", "labelEn": "Postgres DB", ... }

    parsed_nodes = []
    groups_dict = {}  # moduleId -> group info

    # 使用更靈活的方式：逐個匹配物件
    # 先找出所有 {...} 區塊
    object_pattern = r'\{[^}]*?"id"[^}]*?\}'

    for obj_match in re.finditer(object_pattern, nodes_str, re.DOTALL):
        obj_str = obj_match.group(0)

        # 提取各個欄位
        id_match = re.search(r'["\']?id["\']?\s*:\s*["\']([^"\']+)["\']', obj_str)
        labelEn_match = re.search(r'["\']?labelEn["\']?\s*:\s*["\']([^"\']+)["\']', obj_str)
        labelZh_match = re.search(r'["\']?labelZh["\']?\s*:\s*["\']([^"\']+)["\']', obj_str)
        moduleId_match = re.search(r'["\']?moduleId["\']?\s*:\s*(\d+)', obj_str)
        isModuleHeader_match = re.search(r'["\']?isModuleHeader["\']?\s*:\s*(true|false)', obj_str)
        status_match = re.search(r'["\']?status["\']?\s*:\s*["\']([^"\']+)["\']', obj_str)
        script_match = re.search(r'["\']?script["\']?\s*:\s*["\']([^"\']*)["\']', obj_str)
        rationale_match = re.search(r'["\']?rationale["\']?\s*:\s*["\']([^"\']*)["\']', obj_str)

        if not (id_match and labelEn_match and labelZh_match and moduleId_match):
            continue

        node_id = id_match.group(1)
        labelEn = labelEn_match.group(1)
        labelZh = labelZh_match.group(1)
        moduleId = int(moduleId_match.group(1))

        # 判斷是否為 module header：
        # 1. 有 isModuleHeader: true
        # 2. 或 ID 以 mod- 開頭
        isModuleHeader = False
        if isModuleHeader_match and isModuleHeader_match.group(1) == 'true':
            isModuleHeader = True
        elif node_id.startswith('mod-'):
            isModuleHeader = True

        status = status_match.group(1) if status_match else 'unknown'
        script = script_match.group(1) if script_match else ''
        rationale = rationale_match.group(1) if rationale_match else ''

        # 建立節點資料
        node_data = {
            "node_id": node_id,
            "labelEn": labelEn,
            "labelZh": labelZh,
            "script": script,
            "status": status,
            "rationale": rationale,
            "moduleId": moduleId,
            "isModuleHeader": isModuleHeader,
            "parent_group": None,
            "level": None
        }

        parsed_nodes.append(node_data)

        # 建立群組映射
        if isModuleHeader:
            groups_dict[moduleId] = {
                "id": node_id,
                "labelEn": labelEn,
                "labelZh": labelZh,
                "level": 0,  # Module headers 都是 level 0
                "children": []
            }

    # 4. 分配節點到對應的群組
    for node in parsed_nodes:
        if not node["isModuleHeader"]:
            mid = node["moduleId"]
            if mid in groups_dict:
                groups_dict[mid]["children"].append(node["node_id"])
                node["parent_group"] = groups_dict[mid]["id"]

    groups = list(groups_dict.values())

    print(f"[INFO] Parsed {len(parsed_nodes)} nodes and {len(groups)} groups from HTML")
    return parsed_nodes, groups

# ---------------------
# Level 判斷邏輯
# ---------------------
def determine_levels_by_group(nodes: List[Dict], groups: List[Dict], yaml_data: Dict) -> Tuple[List[Dict], List[Dict]]:
    """
    優先級：
    1. YAML levels: 區段
    2. HTML moduleId (isModuleHeader=true → level 0, children → level 1)
    3. YAML builtin: 區段
    4. FALLBACK_BUILTIN_MAP
    """
    ensure_yaml_levels_section(yaml_data)
    yaml_levels = yaml_data.get("levels") or {}

    # 處理 groups
    group_map = {g["id"]: g for g in groups}

    # YAML override for groups
    for key, val in yaml_levels.items():
        if isinstance(val, dict) and "children" in val:
            gid = key
            if gid in group_map:
                if isinstance(val.get("level"), int):
                    group_map[gid]["level"] = val["level"]
                yaml_children = val.get("children") or []
                existing = set(group_map[gid]["children"])
                for c in yaml_children:
                    if c not in existing:
                        group_map[gid]["children"].append(c)

    # 處理 nodes
    node_map = {n["node_id"]: n for n in nodes}

    for nid, node in node_map.items():
        # Module headers 永遠是 level 0
        if node.get("isModuleHeader"):
            node["level"] = 0
            continue

        # 1. YAML levels: 優先
        if nid in yaml_levels and isinstance(yaml_levels[nid], dict):
            lv = yaml_levels[nid].get("level")
            if isinstance(lv, int):
                node["level"] = lv
                continue

        # 2. 如果是 group 的 child，預設 level 1
        if node.get("parent_group"):
            node["level"] = 1
            continue

        # 3. YAML builtin:
        builtin_lv = yaml_data.get("builtin", {}).get(nid)
        if builtin_lv is not None:
            node["level"] = builtin_lv
            continue

        # 4. 硬編碼 fallback
        if nid in FALLBACK_BUILTIN_MAP:
            node["level"] = FALLBACK_BUILTIN_MAP[nid]
            continue

        # 5. 無法決定，留待 CLI 輸入
        node["level"] = None

    return list(node_map.values()), list(group_map.values())

# ---------------------
# CLI 批量 level 賦值
# ---------------------
def cli_batch_assign_levels(unassigned_nodes: List[Dict], yaml_data: Dict) -> Tuple[List[Dict], List[str]]:
    assigned, skipped = [], []
    if not unassigned_nodes:
        return assigned, skipped

    print("\n[NOTICE] Nodes without level (需要手動指定):")
    for i, n in enumerate(unassigned_nodes, 1):
        print(f"{i}. {n['node_id']} ({n.get('labelZh','')}) script={n.get('script','')}")

    inp = input("Enter levels (all 1 / 1,0,1 / skip): ").strip()

    if inp == "":
        skipped = [n["node_id"] for n in unassigned_nodes]
        return assigned, skipped

    # all 1 或 all 0
    if inp.lower().startswith("all"):
        parts = inp.split()
        if len(parts) == 2 and parts[1] in ("0", "1"):
            lv = int(parts[1])
            for n in unassigned_nodes:
                n["level"] = lv
                assigned.append(n)
                ensure_yaml_levels_section(yaml_data)
                yaml_data["levels"].setdefault(n["node_id"], {})
                yaml_data["levels"][n["node_id"]]["level"] = lv
                if not yaml_data["levels"][n["node_id"]].get("label"):
                    yaml_data["levels"][n["node_id"]]["label"] = n.get("labelZh","")
            return assigned, skipped

    # 逗號分隔列表
    tokens = re.split(r"[\s,]+", inp)
    if len(tokens) == len(unassigned_nodes):
        for tok, n in zip(tokens, unassigned_nodes):
            if tok.lower() in ("skip", "s", ""):
                skipped.append(n["node_id"])
                continue
            if tok not in ("0", "1"):
                skipped.append(n["node_id"])
                continue
            lv = int(tok)
            n["level"] = lv
            assigned.append(n)
            ensure_yaml_levels_section(yaml_data)
            yaml_data["levels"].setdefault(n["node_id"], {})
            yaml_data["levels"][n["node_id"]]["level"] = lv
            if not yaml_data["levels"][n["node_id"]].get("label"):
                yaml_data["levels"][n["node_id"]]["label"] = n.get("labelZh","")
        return assigned, skipped

    # 逐個詢問
    for n in unassigned_nodes:
        while True:
            ans = input(f"Set level for {n['node_id']} (0/1/skip): ").strip().lower()
            if ans in ("skip", ""):
                skipped.append(n["node_id"])
                break
            if ans in ("0","1"):
                lv = int(ans)
                n["level"] = lv
                assigned.append(n)
                ensure_yaml_levels_section(yaml_data)
                yaml_data["levels"].setdefault(n["node_id"], {})
                yaml_data["levels"][n["node_id"]]["level"] = lv
                if not yaml_data["levels"][n["node_id"]].get("label"):
                    yaml_data["levels"][n["node_id"]]["label"] = n.get("labelZh","")
                break
            print("Enter 0/1 or skip")

    return assigned, skipped

# ---------------------
# 模糊檔案搜尋
# ---------------------
def find_file_fuzzy(path):
    """在 MODULE_ROOT 中模糊搜尋檔案"""
    if not path:
        return None

    # 1. 完整路徑
    full_path = os.path.join(MODULE_ROOT, path)
    if os.path.exists(full_path):
        return full_path

    # 2. 嘗試加上 .py
    if not path.endswith('.py'):
        py_path = os.path.join(MODULE_ROOT, path + '.py')
        if os.path.exists(py_path):
            return py_path

    # 3. 搜尋檔名
    base = os.path.basename(path)
    for root, dirs, files in os.walk(MODULE_ROOT):
        if base in files:
            return os.path.join(root, base)
        if base + '.py' in files:
            return os.path.join(root, base + '.py')

    return None

# ---------------------
# 建立腳本查找表
# ---------------------
def build_script_lookup(nodes: List[Dict]) -> Dict[str, str]:
    """建立 script 路徑 → node_id 的映射"""
    script_map = {}  # 完整路徑
    module_map = {}  # module.path.format
    basename_map = {}  # 檔名

    for n in nodes:
        nid = n["node_id"]
        script = (n.get("script") or "").strip()
        if not script:
            basename_map[nid] = nid  # 讓 node_id 自己也能匹配
            continue

        # 正規化路徑
        s_norm = script.replace("\\", "/").lstrip("./")
        script_map[s_norm] = nid

        # 建立 module 格式映射
        if s_norm.endswith(".py"):
            mod_path = s_norm[:-3]
            dotted = mod_path.replace("/", ".")
            module_map[dotted] = nid
            basename_map[os.path.basename(mod_path)] = nid
        else:
            basename_map[os.path.splitext(os.path.basename(s_norm))[0]] = nid

        # 也加入 node_id 映射
        basename_map[nid] = nid

    return {"script_exact": script_map, "module": module_map, "basename": basename_map}

# ---------------------
# Edge 檢測 (依賴關係分析)
# ---------------------
def detect_edges_refined(nodes: List[Dict]) -> List[Dict]:
    """使用 AST 分析 Python import 關係"""
    lookup_maps = build_script_lookup(nodes)
    script_exact_map = lookup_maps["script_exact"]
    module_map = lookup_maps["module"]
    basename_map = lookup_maps["basename"]

    edges_set: Set[Tuple[str,str]] = set()

    for n in nodes:
        src_id = n["node_id"]
        script = (n.get("script") or "").strip()
        if not script:
            continue

        file_path = find_file_fuzzy(script)
        if not file_path:
            continue

        try:
            src = read_file(file_path)
            tree = ast.parse(src, filename=file_path)
        except SyntaxError as e:
            print(f"[WARNING] Syntax error in {file_path}: {e}")
            continue
        except Exception as e:
            print(f"[WARNING] Failed to parse {file_path}: {e}")
            continue

        # 分析 import
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name
                    candidates = [imported, imported.split(".")[-1]]
                    for cand in candidates:
                        if cand in module_map:
                            tgt = module_map[cand]
                            if tgt != src_id:
                                edges_set.add((src_id, tgt))
                                break
                        elif cand in basename_map:
                            tgt = basename_map[cand]
                            if tgt != src_id:
                                edges_set.add((src_id, tgt))
                                break

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in module_map:
                    tgt = module_map[module]
                    if tgt != src_id:
                        edges_set.add((src_id, tgt))

                for alias in node.names:
                    name = alias.name
                    if name in module_map:
                        tgt = module_map[name]
                        if tgt != src_id:
                            edges_set.add((src_id, tgt))
                    elif name in basename_map:
                        tgt = basename_map[name]
                        if tgt != src_id:
                            edges_set.add((src_id, tgt))

    return [{"from": f, "to": t} for f, t in sorted(edges_set)]

def detect_edges_from_dataflow(nodes: List[Dict]) -> List[Dict]:
    """基於已知的系統數據流生成 edges

    這個函數定義了系統實際的執行流程和數據流向，
    而不只是程式碼的 import 依賴關係。

    Returns:
        List[Dict]: 數據流 edges
    """
    # 建立 node_id 集合，用於過濾
    node_ids = {n["node_id"] for n in nodes}

    # 定義完整的數據流（基於 generate_answer.py 和系統架構）
    dataflow_edges = [
        # A. Input Layer → Retrieval (3條)
        ("sys-api", "query-decomp"),          # API 發起查詢拆解
        ("sys-api", "knowledge-gap"),         # 並行執行需求偵測
        ("query-decomp", "internal-index"),   # 拆解後送到檢索

        # B. Retrieval Layer ↔ Storage (4條)
        ("internal-index", "sys-vec"),        # 查詢向量資料庫
        ("internal-index", "sys-db"),         # 查詢關聯式資料庫
        ("sys-vec", "internal-index"),        # 向量結果返回
        ("sys-db", "internal-index"),         # 資料庫結果返回

        # C. Retrieval → Ranking (2條)
        ("internal-index", "xgboost"),        # 檢索結果送到ML排序
        ("xgboost", "mmr"),                   # 排序結果送到多樣性過濾

        # D. Ranking → Reasoning (2條)
        ("mmr", "evidence-chain"),            # 過濾後構建證據鏈
        ("evidence-chain", "synthesis"),      # 證據送到綜合模組

        # E. Reasoning ↔ LLM → Output (4條)
        ("synthesis", "sys-llm"),             # 呼叫LLM生成答案
        ("sys-llm", "synthesis"),             # LLM結果返回
        ("synthesis", "sys-api"),             # 答案返回API
        ("sys-api", "sys-fe"),                # API送到前端顯示

        # F. Infrastructure Support (3條)
        ("prompt-guardrails", "sys-llm"),     # 安全檢查保護LLM
        ("xgboost", "sys-ana"),               # 記錄排序數據
        ("sys-ana", "sys-db"),                # 分析數據存入資料庫

        # G. Additional flows (可選，視需求啟用)
        # ("sys-api", "domain-classifier"),   # 領域分類
        # ("domain-classifier", "internal-index"),
        # ("sys-cache", "internal-index"),    # Cache 加速
        # ("internal-index", "sys-cache"),
        # ("knowledge-gap", "iterative-search"), # 遞迴搜尋
        # ("iterative-search", "internal-index"),
        # ("internal-index", "web-search"),   # 網路搜尋
        # ("web-search", "mmr"),
    ]

    # 只保留兩端節點都存在的 edges
    valid_edges = []
    for from_id, to_id in dataflow_edges:
        if from_id in node_ids and to_id in node_ids:
            valid_edges.append({"from": from_id, "to": to_id})

    print(f"[INFO] Generated {len(valid_edges)} dataflow edges (from {len(dataflow_edges)} defined flows)")

    return valid_edges

# ---------------------
# 狀態分析
# ---------------------
def analyze_status(nodes: List[Dict]):
    """分析 Python 模組實現狀態

    策略：
    - 如果檔案存在 → 自動偵測 status (done/partial)
    - 如果檔案不存在 → 保留原有 status（可能是手動設定的未來模組）
    """
    for n in nodes:
        # Module headers 沒有實現
        if n.get("isModuleHeader"):
            n["status"] = "header"
            continue

        script = (n.get("script") or "").strip()
        if not script:
            # 沒有 script 路徑：保留原 status 或設為 notdone
            if not n.get("status") or n.get("status") == "unknown":
                n["status"] = "notdone"
            continue

        script_path = find_file_fuzzy(script)
        if not script_path:
            # 檔案不存在：保留原 status（可能是手動設定的未來模組）
            if not n.get("status") or n.get("status") == "unknown":
                n["status"] = "notdone"
            continue

        # 檔案存在：自動偵測 status
        try:
            src = read_file(script_path)
            tree = ast.parse(src, filename=script_path)
        except Exception:
            n["status"] = "partial"
            continue

        status = "done"
        src_lines = src.splitlines()

        # 檢查是否有 pass, TODO, NotImplementedError
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # 檢查函數/類別體是否只有 pass
                if any(isinstance(x, ast.Pass) for x in ast.walk(node)):
                    status = "partial"
                    break

                # 檢查原始碼
                start = getattr(node, "lineno", None)
                end = getattr(node, "end_lineno", None)
                if start and end:
                    slice_text = "\n".join(src_lines[start-1:end])
                    if any(p in slice_text for p in STATUS_PATTERNS):
                        status = "partial"
                        break

        # 全局檢查
        if status == "done" and any(p in src for p in STATUS_PATTERNS):
            status = "partial"

        n["status"] = status

# ---------------------
# YAML 更新
# ---------------------
def update_yaml_entries(yaml_data: Dict, nodes: List[Dict], groups: List[Dict]) -> Tuple[int, int]:
    """更新 YAML 配置"""
    ensure_yaml_levels_section(yaml_data)
    new_cnt, updated_cnt = 0, 0
    levels_section = yaml_data["levels"]

    # 更新 groups
    for g in groups:
        gid = g["id"]
        if gid not in levels_section:
            levels_section[gid] = {
                "level": g.get("level", 0),
                "children": g.get("children", []),
                "label": g.get("labelZh", ""),
                "description": ""
            }
            new_cnt += 1
        else:
            existing = levels_section[gid]
            changed = False
            existing_children = set(existing.get("children", []) or [])
            for c in g.get("children", []):
                if c not in existing_children:
                    existing.setdefault("children", []).append(c)
                    changed = True
            if changed:
                updated_cnt += 1

    # 更新 nodes
    for n in nodes:
        nid = n["node_id"]
        lvl = n.get("level")
        label = n.get("labelZh", "")

        if nid not in levels_section:
            levels_section[nid] = {
                "level": lvl if isinstance(lvl, int) else None,
                "label": label,
                "description": ""
            }
            new_cnt += 1
        else:
            existing = levels_section[nid]
            changed = False
            if existing.get("level") is None and isinstance(lvl, int):
                existing["level"] = lvl
                changed = True
            if not existing.get("label") and label:
                existing["label"] = label
                changed = True
            if changed:
                updated_cnt += 1

    return new_cnt, updated_cnt

# ---------------------
# 偵測被移除的 YAML 條目
# ---------------------
def detect_removed_yaml_entries(yaml_data: Dict, nodes: List[Dict], groups: List[Dict]) -> List[str]:
    """偵測 YAML 中存在但 HTML 中不存在的條目"""
    ensure_yaml_levels_section(yaml_data)
    parsed_ids = {n["node_id"] for n in nodes}
    parsed_group_ids = {g["id"] for g in groups}
    removed = []

    for key in list(yaml_data["levels"].keys()):
        if key not in parsed_ids and key not in parsed_group_ids:
            removed.append(key)

    return removed

# ---------------------
# 載入舊佈局
# ---------------------
def load_layout_from_json(layout_path: str) -> tuple:
    """從 architecture-diagram.json 載入舊的佈局座標、moduleBoxes 和 edges

    Returns:
        tuple: (old_layout dict, moduleBoxes list, edges list, layout_data dict)
    """
    old_layout = {}
    module_boxes = []
    edges = []
    layout_data = {}  # 保存完整數據以供後續使用

    if not os.path.exists(layout_path):
        return old_layout, module_boxes, edges, layout_data

    try:
        with open(layout_path, 'r', encoding='utf-8') as f:
            layout_data = json.load(f)

        # 建立 node_id → {x, y} 映射
        for node in layout_data.get('nodes', []):
            if node.get('x') is not None and node.get('y') is not None:
                old_layout[node['id']] = {
                    'x': node['x'],
                    'y': node['y']
                }

        # 保留 moduleBoxes
        module_boxes = layout_data.get('moduleBoxes', [])

        # 保留 edges
        edges = layout_data.get('edges', [])

        print(f"[INFO] Loaded layout for {len(old_layout)} nodes from {os.path.basename(layout_path)}")
        if module_boxes:
            print(f"[INFO] Loaded {len(module_boxes)} module boxes from {os.path.basename(layout_path)}")
        if edges:
            print(f"[INFO] Loaded {len(edges)} edges from {os.path.basename(layout_path)}")
    except Exception as e:
        print(f"[WARNING] Could not load layout: {e}")

    return old_layout, module_boxes, edges, layout_data

# ---------------------
# 組合 graphData.json
# ---------------------
def compose_graph_data(nodes: List[Dict], groups: List[Dict], edges: List[Dict]) -> Dict:
    """組合最終的 graphData.json，並保留舊佈局座標、moduleBoxes 和 edges"""

    # 1. 載入舊佈局、moduleBoxes、edges 和完整數據
    old_layout, module_boxes, old_edges, layout_data = load_layout_from_json(LAYOUT_JSON)

    # 2. 組合 groups
    group_nodes = []
    for g in groups:
        group_nodes.append({
            "id": g["id"],
            "type": "group",
            "labelEn": g.get("labelEn", g["id"]),
            "labelZh": g.get("labelZh", ""),
            "level": g.get("level", 0),
            "children": g.get("children", [])
        })

    # 3. 組合 nodes，保留座標
    module_nodes = []
    preserved_count = 0
    processed_node_ids = set()

    for n in nodes:
        node_id = n["node_id"]
        processed_node_ids.add(node_id)

        node_data = {
            "id": node_id,
            "labelEn": n.get("labelEn", node_id),
            "labelZh": n.get("labelZh", ""),
            "moduleId": n.get("moduleId"),
            "status": n.get("status", "unknown"),
            "rationale": n.get("rationale", ""),
            "script": n.get("script", "")
        }

        # 保留 isModuleHeader
        if n.get("isModuleHeader"):
            node_data["isModuleHeader"] = True

        # 4. 保留舊座標
        if node_id in old_layout:
            node_data["x"] = old_layout[node_id]["x"]
            node_data["y"] = old_layout[node_id]["y"]
            preserved_count += 1

            # 保留 workflows 欄位（從 layout_data 查找）
            if layout_data:
                old_node = next((node for node in layout_data.get('nodes', [])
                                 if node.get('id') == node_id), None)
                if old_node and 'workflows' in old_node:
                    node_data['workflows'] = old_node['workflows']
        else:
            # 新節點沒有座標（瀏覽器會自動計算）
            node_data["x"] = None
            node_data["y"] = None

        module_nodes.append(node_data)

    # 🆕 保留 JSON 中存在但 HTML 中不存在的節點（手動新增的節點）
    if layout_data:
        for old_node in layout_data.get('nodes', []):
            old_node_id = old_node.get('id')
            if old_node_id and old_node_id not in processed_node_ids:
                # 這是手動新增的節點，完整保留
                preserved_node = {
                    "id": old_node_id,
                    "labelEn": old_node.get("labelEn", old_node_id),
                    "labelZh": old_node.get("labelZh", ""),
                    "moduleId": old_node.get("moduleId", 0),
                    "status": old_node.get("status", "notdone"),
                    "rationale": old_node.get("rationale", ""),
                    "script": old_node.get("script", ""),
                    "x": old_node.get("x"),
                    "y": old_node.get("y")
                }
                if old_node.get("isModuleHeader"):
                    preserved_node["isModuleHeader"] = True
                if "workflows" in old_node:
                    preserved_node["workflows"] = old_node["workflows"]

                module_nodes.append(preserved_node)
                print(f"[INFO] Preserved manually-added node: {old_node_id} ({old_node.get('labelZh', '')})")

    if preserved_count > 0:
        print(f"[INFO] Preserved layout for {preserved_count}/{len(nodes)} nodes")

    # 5. 決定使用哪些 edges：
    #    方案 A：完全信任 JSON，只在初始化時使用 dataflow
    #    - 優先使用 architecture-diagram.json 的 edges（尊重手動編輯）
    #    - 只在沒有舊 edges 時才使用 dataflow 初始化
    valid_node_ids = {n["id"] for n in module_nodes}

    if old_edges:
        # 使用舊的 edges 作為基礎（尊重手動編輯：新增、刪除都保留）
        valid_old_edges = [
            e for e in old_edges
            if e.get("from") in valid_node_ids and e.get("to") in valid_node_ids
        ]

        removed_edges_count = len(old_edges) - len(valid_old_edges)
        if removed_edges_count > 0:
            print(f"[INFO] Removed {removed_edges_count} invalid edges (nodes not found)")

        final_edges = valid_old_edges
        print(f"[INFO] Using {len(final_edges)} edges from architecture-diagram.json (手動編輯已保留)")
    else:
        # 沒有舊 edges，使用 dataflow 初始化（首次執行）
        final_edges = edges if edges else []
        print(f"[INFO] Initialized {len(final_edges)} edges from dataflow definition (首次初始化)")

    # 6. 組合完整的 graphData（包含 moduleBoxes）
    result = {
        "nodes": module_nodes,
        "edges": final_edges
    }

    # 只有在有 moduleBoxes 的時候才加入
    if module_boxes:
        result["moduleBoxes"] = module_boxes

    return result

# ---------------------
# 修復 JSON 中的換行符（用於嵌入 HTML）
# ---------------------
def escape_newlines_in_json(json_str: str) -> str:
    """
    將 JSON 字符串值內的實際換行符替換為 \\n 轉義序列

    這是為了避免將 JSON 嵌入 HTML <script> 時出現 JavaScript 語法錯誤
    """
    result = []
    in_string = False
    backslash = chr(92)  # '\'
    quote = chr(34)      # '"'
    newline = chr(10)    # '\n'

    for i, char in enumerate(json_str):
        prev = json_str[i-1] if i > 0 else ''

        # 檢測字符串邊界（忽略轉義的引號 \"）
        if char == quote and prev != backslash:
            in_string = not in_string
            result.append(char)
        # 在字符串內遇到換行符，替換為 \n
        elif in_string and char == newline:
            result.append(backslash + 'n')
        else:
            result.append(char)

    return ''.join(result)


# ---------------------
# 更新 HTML 中的 graphData
# ---------------------
def update_html_graphdata(html_path: str, new_graphdata: Dict) -> bool:
    """
    將新的 graphData 寫回 architecture.html

    重寫邏輯：
    1. 使用二進制模式讀寫，避免編碼問題
    2. json.dumps 後直接處理字節，確保轉義序列正確保留
    3. 使用 newline='' 參數避免 Windows 換行符問題
    """
    try:
        # 1. 讀取原始 HTML（使用 UTF-8）
        with open(html_path, 'r', encoding='utf-8', newline='') as f:
            html_content = f.read()

        # 2. 生成 JSON 字符串（json.dumps 會正確轉義 \n）
        # ensure_ascii=False: 保留中文字符
        # indent=4: 4空格縮進（後續會調整為8空格）
        json_str = json.dumps(new_graphdata, ensure_ascii=False, indent=4)

        # 3. 調整縮排：每行添加 8 個空格（以符合 HTML 中的縮排）
        lines = json_str.split('\n')
        indented_lines = []
        for line in lines:
            if line.strip():  # 非空行
                indented_lines.append('        ' + line)
            else:  # 空行
                indented_lines.append(line)

        indented_json = '\n'.join(indented_lines)

        # 4. 組合 JavaScript 代碼
        new_js = f"const graphData = {indented_json};"

        # 5. 使用正則替換 HTML 中的 graphData
        # 匹配模式：const graphData = {...};
        pattern = r'const graphData\s*=\s*\{[\s\S]*?\n\s*\};'

        if not re.search(pattern, html_content):
            print("[ERROR] Could not find 'const graphData = {...};' pattern in HTML")
            return False

        updated_html = re.sub(pattern, new_js, html_content, count=1)

        # 6. 備份原始 HTML
        backup_path = html_path + '.backup'
        with open(backup_path, 'w', encoding='utf-8', newline='') as f:
            f.write(html_content)

        # 7. 寫回 HTML（使用 newline='' 避免 Windows 換行符轉換）
        with open(html_path, 'w', encoding='utf-8', newline='') as f:
            f.write(updated_html)

        # 8. 🆕 後處理修復：讀回文件並修復字符串值內的實際換行符
        # 問題：Python 文件寫入時會將某些轉義序列轉換為實際字符
        # 解決：寫入後立即讀回並應用 escape_newlines_in_json 修復
        with open(html_path, 'r', encoding='utf-8', newline='') as f:
            html_after_write = f.read()

        # 找到 graphData 區域並修復
        match = re.search(r'(const graphData\s*=\s*)(\{[\s\S]*?\n\s*\});', html_after_write)
        if match:
            prefix = match.group(1)
            graphdata_str = match.group(2)

            # 應用修復函數
            fixed_graphdata = escape_newlines_in_json(graphdata_str)

            # 重建 HTML
            fixed_html = html_after_write[:match.start()] + prefix + fixed_graphdata + ';' + html_after_write[match.end():]

            # 再次寫回
            with open(html_path, 'w', encoding='utf-8', newline='') as f:
                f.write(fixed_html)

        print(f"[OK] Updated graphData in {os.path.basename(html_path)}")
        print(f"[INFO] Backup saved to {os.path.basename(backup_path)}")
        return True

    except Exception as e:
        print(f"[ERROR] Failed to update HTML: {e}")
        import traceback
        traceback.print_exc()
        return False

# ---------------------
# Main
# ---------------------
def main():
    print("=== update_architecture.py (NLWeb 版本) start ===")

    # 載入 YAML
    yaml_data = load_yaml(YAML_PATH) or {"levels": {}}

    # 🆕 檢查是否有 architecture-diagram.json，如果有則優先使用
    if os.path.exists(LAYOUT_JSON):
        try:
            with open(LAYOUT_JSON, 'r', encoding='utf-8') as f:
                layout_data = json.load(f)

            if layout_data and 'nodes' in layout_data and len(layout_data['nodes']) > 0:
                print(f"[INFO] Found {LAYOUT_JSON} with {len(layout_data['nodes'])} nodes")
                print("[INFO] Using JSON as source of truth (skipping HTML parsing)")

                # 直接從 JSON 構建 parsed_nodes
                parsed_nodes = []
                parsed_groups_dict = {}

                for node in layout_data['nodes']:
                    node_data = {
                        "node_id": node.get('id'),
                        "labelEn": node.get('labelEn', ''),
                        "labelZh": node.get('labelZh', ''),
                        "moduleId": node.get('moduleId', 0),
                        "status": node.get('status', 'unknown'),
                        "rationale": node.get('rationale', ''),
                        "script": node.get('script', ''),
                        "isModuleHeader": node.get('isModuleHeader', False)
                    }
                    parsed_nodes.append(node_data)

                    # 建立 groups
                    if node_data['isModuleHeader']:
                        mid = node_data['moduleId']
                        parsed_groups_dict[mid] = {
                            "id": node_data["node_id"],
                            "labelEn": node_data['labelEn'],
                            "labelZh": node_data['labelZh'],
                            "level": 0,
                            "children": []
                        }

                # 分配 children 到 groups
                for node_data in parsed_nodes:
                    if not node_data['isModuleHeader']:
                        mid = node_data['moduleId']
                        if mid in parsed_groups_dict:
                            parsed_groups_dict[mid]['children'].append(node_data['node_id'])

                parsed_groups = list(parsed_groups_dict.values())
                print(f"[INFO] Loaded {len(parsed_nodes)} nodes and {len(parsed_groups)} groups from JSON")
            else:
                # JSON 為空，從 HTML 解析
                print("[INFO] JSON is empty, parsing from HTML")
                parsed_nodes, parsed_groups = parse_graphdata_from_html(HTML_FILE)
        except Exception as e:
            print(f"[WARNING] Failed to load JSON: {e}")
            print("[INFO] Falling back to HTML parsing")
            parsed_nodes, parsed_groups = parse_graphdata_from_html(HTML_FILE)
    else:
        # 沒有 JSON，從 HTML 解析
        print("[INFO] No JSON found, parsing from HTML")
        parsed_nodes, parsed_groups = parse_graphdata_from_html(HTML_FILE)

    # 判斷 levels
    nodes_with_levels, groups_final = determine_levels_by_group(parsed_nodes, parsed_groups, yaml_data)

    # CLI 批量賦值
    unassigned = [n for n in nodes_with_levels if n.get("level") is None]
    assigned_via_cli, skipped = cli_batch_assign_levels(unassigned, yaml_data)

    # 合併結果
    nid_to_node = {n["node_id"]: n for n in nodes_with_levels}
    for a in assigned_via_cli:
        nid_to_node[a["node_id"]] = a
    final_nodes = list(nid_to_node.values())

    # 更新 YAML
    new_cnt, updated_cnt = update_yaml_entries(yaml_data, final_nodes, groups_final)

    # 偵測移除的條目
    removed = detect_removed_yaml_entries(yaml_data, final_nodes, groups_final)
    if removed:
        for r in removed:
            print(f"[WARNING] YAML contains '{r}' not in HTML. Manual confirmation required.")

    # 狀態分析
    print("\n[INFO] Analyzing implementation status...")
    analyze_status(final_nodes)

    # 依賴關係分析
    print("[INFO] Detecting edges (data flow analysis)...")
    edges = detect_edges_from_dataflow(final_nodes)

    # 組合 graphData（含佈局保留）
    print("[INFO] Composing graphData (preserving layout)...")
    graph = compose_graph_data(final_nodes, groups_final, edges)

    # 更新 HTML
    print("[INFO] Updating architecture.html...")
    html_updated = update_html_graphdata(HTML_FILE, graph)

    # 寫入 JSON 檔案
    with open(GRAPH_JSON, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    # 寫入 YAML
    save_yaml(YAML_PATH, yaml_data)

    # 總結
    print("\n" + "="*60)
    print("[SUMMARY]")
    print(f"[OK] {len(final_nodes)} module nodes processed")
    print(f"[OK] {len(groups_final)} groups processed")
    print(f"[OK] {new_cnt} new YAML entries added")
    print(f"[OK] {updated_cnt} YAML entries updated")
    print(f"[OK] {len(edges)} edges detected")

    # 狀態統計
    status_count = {"done": 0, "partial": 0, "notdone": 0, "header": 0, "unknown": 0}
    for n in final_nodes:
        st = n.get("status", "unknown")
        status_count[st] = status_count.get(st, 0) + 1

    print(f"\n[Status Summary]")
    print(f"  Done: {status_count['done']}")
    print(f"  Partial: {status_count['partial']}")
    print(f"  Not Done: {status_count['notdone']}")
    print(f"  Headers: {status_count['header']}")

    if skipped:
        print(f"\n[WARN] {len(skipped)} nodes skipped: {', '.join(skipped)}")
    if removed:
        print(f"[WARN] {len(removed)} YAML entries not in HTML: {', '.join(removed)}")

    print("\n[Output Files]")
    print(f"  {YAML_PATH} - Level configuration")
    print(f"  {GRAPH_JSON} - Graph data for visualization")
    if html_updated:
        print(f"  {HTML_FILE} - Architecture diagram (UPDATED)")
        print(f"  {HTML_FILE}.backup - Original backup")
    print("="*60)
    print("=== Done ===")

    if html_updated:
        print("\n[NEXT STEPS]")
        print("1. Open static/architecture.html in browser to verify")
        print("2. Adjust layout in edit mode if needed")
        print("3. Export to architecture-diagram.json to save layout")
        print("4. Run this script again to preserve layout")

if __name__ == "__main__":
    main()
