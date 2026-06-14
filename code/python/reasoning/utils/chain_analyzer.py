"""
Reasoning Chain Analyzer for impact propagation and critical node detection.
Optimized version with memoization and topological sorting.
"""

from typing import List, Dict, Set, Tuple
from collections import defaultdict, deque
from reasoning.schemas_enhanced import (
    ArgumentNode, StructuredWeakness, NodeImpactAnalysis,
    ReasoningChainAnalysis
)
from misc.logger.logging_config_helper import get_configured_logger

logger = get_configured_logger("reasoning.chain_analyzer")


class ReasoningChainAnalyzer:
    """Analyze argument graph for impact propagation and critical nodes."""

    def __init__(self, nodes: List[ArgumentNode], weaknesses: List[StructuredWeakness] = None):
        """
        Initialize analyzer with nodes and optional weaknesses.

        Args:
            nodes: List of ArgumentNode with depends_on relationships
            weaknesses: Optional list of StructuredWeakness from Critic
        """
        self.nodes = nodes
        self.weaknesses = weaknesses or []
        self.node_map = {n.node_id: n for n in nodes}

        # Build adjacency lists
        self.forward_graph = defaultdict(list)  # node -> [children]
        self.backward_graph = defaultdict(list)  # node -> [parents]
        self._build_graph()

    def _build_graph(self):
        """Build forward and backward adjacency lists from depends_on."""
        for node in self.nodes:
            for parent_id in node.depends_on:
                if parent_id in self.node_map:
                    self.forward_graph[parent_id].append(node.node_id)
                    self.backward_graph[node.node_id].append(parent_id)
                else:
                    logger.warning(
                        f"Node {node.node_id[:8]} references non-existent parent {parent_id[:8]}"
                    )

    def detect_cycles(self) -> Tuple[bool, str]:
        """
        Detect cycles using DFS with recursion stack.

        Returns:
            (has_cycle, cycle_description)
        """
        visited = set()
        rec_stack = set()
        cycle_path = []

        def dfs(node_id, path):
            visited.add(node_id)
            rec_stack.add(node_id)
            path.append(node_id)

            for child_id in self.forward_graph.get(node_id, []):
                if child_id not in visited:
                    if dfs(child_id, path):
                        return True
                elif child_id in rec_stack:
                    # Cycle detected
                    cycle_start = path.index(child_id)
                    cycle_path.extend(path[cycle_start:])
                    return True

            path.pop()
            rec_stack.remove(node_id)
            return False

        # Check all components
        for node_id in self.node_map:
            if node_id not in visited:
                if dfs(node_id, []):
                    claims = [self.node_map[nid].claim[:30] + "..." for nid in cycle_path[:3]]
                    return True, f"Cycle detected: {' -> '.join(claims)}..."

        return False, None

    def _get_downstream_impact(self, node_id: str, memo: Dict[str, Set[str]]) -> Set[str]:
        """
        Calculate downstream impact with memoization.

        Args:
            node_id: Starting node
            memo: Memoization cache

        Returns:
            Set of all affected node IDs
        """
        if node_id in memo:
            return memo[node_id]

        impact_set = set()
        children = self.forward_graph.get(node_id, [])

        for child in children:
            impact_set.add(child)
            impact_set.update(self._get_downstream_impact(child, memo))

        memo[node_id] = impact_set
        return impact_set

    def calculate_impact(self) -> Dict[str, NodeImpactAnalysis]:
        """
        Calculate impact (affects_count) for each node with memoization.

        Returns:
            Dict mapping node_id to NodeImpactAnalysis
        """
        impact_map = {}
        memo = {}  # Memoization cache

        for node_id in self.node_map:
            affected = self._get_downstream_impact(node_id, memo)

            # Determine criticality
            node = self.node_map[node_id]
            is_critical, reason = self._is_critical_node(node, len(affected))

            impact_map[node_id] = NodeImpactAnalysis(
                node_id=node_id,
                affects_count=len(affected),
                affected_node_ids=list(affected),
                is_critical=is_critical,
                criticality_reason=reason
            )

        return impact_map

    def _is_critical_node(self, node: ArgumentNode, affects_count: int) -> Tuple[bool, str]:
        """
        Determine if node is critical (high impact + low confidence or weakness).

        Args:
            node: ArgumentNode to evaluate
            affects_count: Number of downstream nodes affected

        Returns:
            (is_critical, criticality_reason)
        """
        reasons = []

        # Check confidence score
        confidence_score = node.confidence_score or self._infer_score(node.confidence)
        if confidence_score < 6.0 and affects_count >= 2:
            reasons.append(f"低信心度 ({confidence_score}/10) 影響 {affects_count} 個推論")

        # Check weaknesses
        node_weaknesses = [w for w in self.weaknesses if w.node_id == node.node_id]
        critical_weaknesses = [w for w in node_weaknesses if w.severity == "critical"]
        if critical_weaknesses and affects_count >= 1:
            reasons.append(f"{len(critical_weaknesses)} 個嚴重問題影響下游推論")

        is_critical = len(reasons) > 0
        reason = "; ".join(reasons) if reasons else None

        return is_critical, reason

    def _infer_score(self, confidence: str) -> float:
        """Infer numerical score from confidence level."""
        mapping = {"high": 8.0, "medium": 5.0, "low": 2.0}
        return mapping.get(confidence, 5.0)

    def topological_sort(self) -> List[str]:
        """
        Perform topological sort using Kahn's Algorithm.

        Returns:
            List of node_ids in topological order (parents before children)
        """
        in_degree = {nid: len(self.backward_graph[nid]) for nid in self.node_map}
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        topo_order = []

        while queue:
            current = queue.popleft()
            topo_order.append(current)

            for child_id in self.forward_graph[current]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    queue.append(child_id)

        # If graph has cycles, topo_order won't include all nodes
        if len(topo_order) < len(self.node_map):
            logger.warning("Topological sort incomplete (likely due to cycles)")
            # Return original order + missing nodes
            missing = [nid for nid in self.node_map if nid not in topo_order]
            topo_order.extend(missing)

        return topo_order

    def calculate_max_depth(self) -> int:
        """
        Calculate maximum depth of reasoning chain.

        Returns:
            Maximum depth (0 for single-node graphs)
        """
        depth = {}
        in_degree = {nid: len(self.backward_graph[nid]) for nid in self.node_map}
        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])

        for nid in queue:
            depth[nid] = 0

        while queue:
            current = queue.popleft()
            current_depth = depth[current]

            for child_id in self.forward_graph[current]:
                in_degree[child_id] -= 1
                depth[child_id] = max(depth.get(child_id, 0), current_depth + 1)

                if in_degree[child_id] == 0:
                    queue.append(child_id)

        return max(depth.values()) if depth else 0

    def check_logic_consistency(self) -> int:
        """
        Check for logic inflation (Weakest Link Principle).

        Detects cases where child node has higher confidence than parent nodes.

        Returns:
            Count of inconsistencies detected
        """
        inconsistency_count = 0

        for node in self.nodes:
            if not node.depends_on:
                continue  # Axioms have no parents

            child_score = node.confidence_score or self._infer_score(node.confidence)

            for parent_id in node.depends_on:
                parent = self.node_map.get(parent_id)
                if not parent:
                    continue

                parent_score = parent.confidence_score or self._infer_score(parent.confidence)

                # Logic inflation: child confidence > parent + threshold
                if child_score > parent_score + 3.0:  # Threshold: 3 points
                    warning = f"Confidence inflated relative to premise (parent: {parent_score:.1f}, child: {child_score:.1f})"
                    node.logic_warnings.append(warning)
                    inconsistency_count += 1
                    logger.warning(
                        f"Logic inflation detected: '{node.claim[:40]}...' "
                        f"(score {child_score:.1f}) depends on '{parent.claim[:40]}...' "
                        f"(score {parent_score:.1f})"
                    )

        return inconsistency_count

    def analyze(self) -> ReasoningChainAnalysis:
        """
        Perform complete reasoning chain analysis.

        Returns:
            ReasoningChainAnalysis with impact, depth, cycles, and critical nodes
        """
        logger.info(f"Analyzing reasoning chain: {len(self.nodes)} nodes")

        # Detect cycles
        has_cycles, cycle_details = self.detect_cycles()
        if has_cycles:
            logger.warning(f"Cycle detected in reasoning chain: {cycle_details}")

        # Topological sort
        topo_order = self.topological_sort()
        logger.info(f"Topological order: {len(topo_order)} nodes sorted")

        # Calculate impact (with memoization)
        impact_map = self.calculate_impact()

        # Find critical nodes (sorted by affects_count descending)
        critical_nodes = sorted(
            [impact for impact in impact_map.values() if impact.is_critical],
            key=lambda x: x.affects_count,
            reverse=True
        )

        if critical_nodes:
            logger.warning(f"Found {len(critical_nodes)} critical nodes")
            for cn in critical_nodes[:3]:  # Log top 3
                node = self.node_map[cn.node_id]
                logger.warning(
                    f"  - Critical: '{node.claim[:40]}...' "
                    f"(affects {cn.affects_count}, reason: {cn.criticality_reason})"
                )

        # Check logic consistency
        inconsistencies = self.check_logic_consistency()
        if inconsistencies > 0:
            logger.warning(f"Found {inconsistencies} logic inflation warnings")

        # Calculate max depth
        max_depth = self.calculate_max_depth()
        logger.info(f"Reasoning chain max depth: {max_depth}")

        return ReasoningChainAnalysis(
            total_nodes=len(self.nodes),
            max_depth=max_depth,
            topological_order=topo_order,
            critical_nodes=critical_nodes,
            has_cycles=has_cycles,
            cycle_details=cycle_details,
            logic_inconsistencies=inconsistencies
        )
