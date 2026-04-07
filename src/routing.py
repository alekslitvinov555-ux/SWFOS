from __future__ import annotations

from dataclasses import dataclass
import math

import networkx as nx

DELAY_PENALTY_PER_HOUR = 3000.0
OPERATING_COST_PER_KM = 300.0
ZERO_LOCO_WAIT_HOURS = 12.0


@dataclass
class RouteResult:
    path: list[str]
    total_cost: float  # economic cost, UAH
    segment_costs: list[float]
    total_time_hours: float
    segment_times_hours: list[float]
    total_distance_km: float
    segment_distances_km: list[float]


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


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    q = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(q), math.sqrt(1 - q))


def _edge_distance_km(graph: nx.DiGraph, u: str, v: str, edge_data: dict) -> float:
    waypoints = edge_data.get("waypoints", []) or []
    points = [[float(graph.nodes[u]["lat"]), float(graph.nodes[u]["lon"])]]
    points.extend([[float(p[0]), float(p[1])] for p in waypoints if len(p) >= 2])
    points.append([float(graph.nodes[v]["lat"]), float(graph.nodes[v]["lon"])])

    distance_km = 0.0
    for a, b in zip(points[:-1], points[1:]):
        distance_km += _haversine_km(a[0], a[1], b[0], b[1])
    return distance_km


def _segment_time_hours(graph: nx.DiGraph, v: str, edge_data: dict) -> float:
    base_time = float(edge_data.get("base_time", 1.0))
    edge_capacity = float(edge_data.get("max_capacity", 1.0))
    edge_flow = float(edge_data.get("current_flow", 0.0))

    destination_data = graph.nodes[v]
    node_capacity = float(destination_data.get("capacity", 1.0))
    node_load = float(destination_data.get("current_load", 0.0))
    available_locomotives = int(destination_data.get("available_locomotives", 5))

    congestion_penalty = base_time * (
        _edge_penalty(edge_capacity, edge_flow) + _station_penalty(node_capacity, node_load)
    )
    loco_wait_time = ZERO_LOCO_WAIT_HOURS if available_locomotives <= 0 else 0.0
    return base_time + congestion_penalty + loco_wait_time


def calculate_route_cost(
    graph: nx.DiGraph,
    route: list[str],
    time_hours: float | None = None,
) -> float:
    if len(route) < 2:
        return 0.0

    if time_hours is None:
        time_hours = sum(_segment_time_hours(graph, v, graph[u][v]) for u, v in zip(route[:-1], route[1:]))

    distance_km = sum(_edge_distance_km(graph, u, v, graph[u][v]) for u, v in zip(route[:-1], route[1:]))
    return time_hours * DELAY_PENALTY_PER_HOUR + distance_km * OPERATING_COST_PER_KM


def make_edge_cost_function(graph: nx.DiGraph):
    def edge_cost(u: str, v: str, edge_data: dict) -> float:
        segment_time = _segment_time_hours(graph, v, edge_data)
        segment_distance_km = _edge_distance_km(graph, u, v, edge_data)
        return segment_time * DELAY_PENALTY_PER_HOUR + segment_distance_km * OPERATING_COST_PER_KM

    return edge_cost


def find_optimal_route(graph: nx.DiGraph, source: str, target: str) -> RouteResult:
    """
    Find the lowest-cost economic route using Dijkstra.
    """
    path = nx.dijkstra_path(graph, source=source, target=target, weight=make_edge_cost_function(graph))
    segment_times_hours = [_segment_time_hours(graph, v, graph[u][v]) for u, v in zip(path[:-1], path[1:])]
    segment_distances_km = [_edge_distance_km(graph, u, v, graph[u][v]) for u, v in zip(path[:-1], path[1:])]
    segment_costs = [
        t * DELAY_PENALTY_PER_HOUR + d * OPERATING_COST_PER_KM
        for t, d in zip(segment_times_hours, segment_distances_km)
    ]

    return RouteResult(
        path=path,
        total_cost=sum(segment_costs),
        segment_costs=segment_costs,
        total_time_hours=sum(segment_times_hours),
        segment_times_hours=segment_times_hours,
        total_distance_km=sum(segment_distances_km),
        segment_distances_km=segment_distances_km,
    )


def find_shortest_distance_route(graph: nx.DiGraph, source: str, target: str) -> RouteResult:
    """
    Baseline route: shortest geometric distance, ignoring congestion and locomotive availability.
    """
    path = nx.dijkstra_path(
        graph,
        source=source,
        target=target,
        weight=lambda u, v, edge_data: _edge_distance_km(graph, u, v, edge_data),
    )
    segment_times_hours = [float(graph[u][v].get("base_time", 1.0)) for u, v in zip(path[:-1], path[1:])]
    segment_distances_km = [_edge_distance_km(graph, u, v, graph[u][v]) for u, v in zip(path[:-1], path[1:])]
    segment_costs = [
        t * DELAY_PENALTY_PER_HOUR + d * OPERATING_COST_PER_KM
        for t, d in zip(segment_times_hours, segment_distances_km)
    ]

    return RouteResult(
        path=path,
        total_cost=sum(segment_costs),
        segment_costs=segment_costs,
        total_time_hours=sum(segment_times_hours),
        segment_times_hours=segment_times_hours,
        total_distance_km=sum(segment_distances_km),
        segment_distances_km=segment_distances_km,
    )
