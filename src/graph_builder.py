from __future__ import annotations

import json
from pathlib import Path

import networkx as nx


def _read_json(file_path: Path) -> list[dict]:
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_waypoints(raw_waypoints: list[list[float]] | None) -> list[list[float]]:
    if not raw_waypoints:
        return []

    normalized: list[list[float]] = []
    for point in raw_waypoints:
        if len(point) < 2:
            continue
        normalized.append([float(point[0]), float(point[1])])

    return normalized


def build_graph(stations_path: str | Path, edges_path: str | Path) -> nx.DiGraph:
    """
    Build a directed rail network graph from station and edge JSON files.

    Nodes: station names with capacity/current_load/coordinates.
    Edges: track segments with base_time/max_capacity/current_flow.
    """
    stations_path = Path(stations_path)
    edges_path = Path(edges_path)

    stations = _read_json(stations_path)
    edges = _read_json(edges_path)

    graph = nx.DiGraph()

    for station in stations:
        graph.add_node(
            station["name"],
            capacity=float(station["capacity"]),
            current_load=float(station["current_load"]),
            lat=float(station["lat"]),
            lon=float(station["lon"]),
        )

    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        waypoints = _normalize_waypoints(edge.get("waypoints"))

        edge_attrs = {
            "base_time": float(edge["base_time"]),
            "max_capacity": float(edge["max_capacity"]),
            "current_flow": float(edge.get("current_flow", 0.0)),
            "waypoints": waypoints,
        }

        graph.add_edge(source, target, **edge_attrs)

        if edge.get("bidirectional", True):
            reverse_edge_attrs = {
                "base_time": edge_attrs["base_time"],
                "max_capacity": edge_attrs["max_capacity"],
                "current_flow": edge_attrs["current_flow"],
                "waypoints": [point[:] for point in reversed(waypoints)],
            }
            graph.add_edge(target, source, **reverse_edge_attrs)

    return graph
