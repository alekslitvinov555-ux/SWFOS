from __future__ import annotations

from dataclasses import dataclass

import networkx as nx


@dataclass
class RouteResult:
    path: list[str]
    total_cost: float
    segment_costs: list[float]


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 1.0


def _station_penalty(capacity: float, current_load: float) -> float:
    """
    Penalize stations nearing full load.

    Key behavior:
    - Smooth increase below 90%.
    - Strong penalty above 90% to push route alternatives.
    """
    load_ratio = _safe_ratio(current_load, capacity)

    if load_ratio >= 1.0:
        return 10.0
    if load_ratio >= 0.9:
        return 4.0 + 20.0 * (load_ratio - 0.9)
    return 0.5 * (load_ratio**2)


def _edge_penalty(max_capacity: float, current_flow: float) -> float:
    flow_ratio = _safe_ratio(current_flow, max_capacity)

    if flow_ratio >= 1.0:
        return 6.0
    if flow_ratio >= 0.9:
        return 2.0 + 10.0 * (flow_ratio - 0.9)
    return 0.4 * (flow_ratio**2)


def make_edge_cost_function(graph: nx.DiGraph):
    def edge_cost(u: str, v: str, edge_data: dict) -> float:
        base_time = float(edge_data.get("base_time", 1.0))
        edge_capacity = float(edge_data.get("max_capacity", 1.0))
        edge_flow = float(edge_data.get("current_flow", 0.0))

        destination_data = graph.nodes[v]
        node_capacity = float(destination_data.get("capacity", 1.0))
        node_load = float(destination_data.get("current_load", 0.0))

        congestion_multiplier = 1.0 + _edge_penalty(edge_capacity, edge_flow) + _station_penalty(
            node_capacity, node_load
        )
        return base_time * congestion_multiplier

    return edge_cost


def find_optimal_route(graph: nx.DiGraph, source: str, target: str) -> RouteResult:
    """
    Find the lowest-cost route using Dijkstra and custom congestion-aware cost.
    """
    weight = make_edge_cost_function(graph)
    path = nx.dijkstra_path(graph, source=source, target=target, weight=weight)

    segment_costs: list[float] = []
    for u, v in zip(path[:-1], path[1:]):
        segment_costs.append(weight(u, v, graph[u][v]))

    return RouteResult(
        path=path,
        total_cost=sum(segment_costs),
        segment_costs=segment_costs,
    )
