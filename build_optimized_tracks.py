from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
RAIL_GEOJSON_PATH = DATA_DIR / "railways_southern_ukraine.geojson"
STATIONS_PATH = DATA_DIR / "stations.json"
EDGES_PATH = DATA_DIR / "edges.json"
OUTPUT_PATH = DATA_DIR / "optimized_tracks.json"

SIMPLIFY_TOLERANCE_DEG = 0.005  # ~500m latitude scale
MAX_FALLBACK_STRAIGHT_KM = 35.0


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


def _haversine_km(a: Point, b: Point) -> float:
    r = 6371.0
    phi1 = math.radians(a.lat)
    phi2 = math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlambda = math.radians(b.lon - a.lon)
    q = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(q), math.sqrt(1 - q))


def _perpendicular_distance(point: Point, start: Point, end: Point) -> float:
    x0, y0 = point.lon, point.lat
    x1, y1 = start.lon, start.lat
    x2, y2 = end.lon, end.lat

    if x1 == x2 and y1 == y2:
        return math.hypot(x0 - x1, y0 - y1)

    num = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
    den = math.hypot(y2 - y1, x2 - x1)
    return num / den


def _rdp(points: list[Point], epsilon: float) -> list[Point]:
    if len(points) <= 2:
        return points

    start = points[0]
    end = points[-1]

    max_dist = -1.0
    index = -1
    for i in range(1, len(points) - 1):
        dist = _perpendicular_distance(points[i], start, end)
        if dist > max_dist:
            max_dist = dist
            index = i

    if max_dist > epsilon:
        left = _rdp(points[: index + 1], epsilon)
        right = _rdp(points[index:], epsilon)
        return left[:-1] + right

    return [start, end]


def _parse_geojson_lines(geojson: dict) -> list[list[Point]]:
    lines: list[list[Point]] = []
    for feature in geojson.get("features", []):
        geom = feature.get("geometry", {})
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])

        if gtype == "LineString":
            line = [Point(lat=float(c[1]), lon=float(c[0])) for c in coords]
            if len(line) >= 2:
                lines.append(line)
        elif gtype == "MultiLineString":
            for part in coords:
                line = [Point(lat=float(c[1]), lon=float(c[0])) for c in part]
                if len(line) >= 2:
                    lines.append(line)
    return lines


def _build_graph_from_lines(lines: list[list[Point]]) -> nx.Graph:
    graph = nx.Graph()
    for line in lines:
        simplified = _rdp(line, SIMPLIFY_TOLERANCE_DEG)
        if len(simplified) < 2:
            continue

        for a, b in zip(simplified[:-1], simplified[1:]):
            n1 = (round(a.lat, 6), round(a.lon, 6))
            n2 = (round(b.lat, 6), round(b.lon, 6))
            w = _haversine_km(Point(*n1), Point(*n2))
            if graph.has_edge(n1, n2):
                if w < graph[n1][n2]["weight"]:
                    graph[n1][n2]["weight"] = w
            else:
                graph.add_edge(n1, n2, weight=w)
    return graph


def _largest_connected_component(graph: nx.Graph) -> nx.Graph:
    if graph.number_of_nodes() == 0:
        return graph
    largest_nodes = max(nx.connected_components(graph), key=len)
    return graph.subgraph(largest_nodes).copy()


def _nearest_node(graph_nodes: list[tuple[float, float]], target: Point) -> tuple[float, float] | None:
    if not graph_nodes:
        return None
    return min(graph_nodes, key=lambda n: _haversine_km(Point(n[0], n[1]), target))


def _load_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_optimized_tracks() -> dict:
    with RAIL_GEOJSON_PATH.open("r", encoding="utf-8") as f:
        geojson = json.load(f)

    lines = _parse_geojson_lines(geojson)
    raw_graph = _build_graph_from_lines(lines)
    graph = _largest_connected_component(raw_graph)

    stations = _load_json(STATIONS_PATH)
    edges = _load_json(EDGES_PATH)

    nodes_list = list(graph.nodes)
    station_to_node: dict[str, tuple[float, float]] = {}

    for station in stations:
        name = station["name"]
        p = Point(float(station["lat"]), float(station["lon"]))
        nearest = _nearest_node(nodes_list, p)
        if nearest is not None:
            station_to_node[name] = nearest

    station_paths: dict[str, list[list[float]]] = {}
    unresolved_pairs: list[str] = []

    for edge in edges:
        u = edge["source"]
        v = edge["target"]
        pair_candidates = [(u, v)]
        if edge.get("bidirectional", True):
            pair_candidates.append((v, u))

        for a, b in pair_candidates:
            if a not in station_to_node or b not in station_to_node:
                unresolved_pairs.append(f"{a}__{b}:no_station_snap")
                continue

            source_node = station_to_node[a]
            target_node = station_to_node[b]

            try:
                path_nodes = nx.shortest_path(graph, source=source_node, target=target_node, weight="weight")
                station_paths[f"{a}__{b}"] = [[float(lat), float(lon)] for lat, lon in path_nodes]
            except nx.NetworkXNoPath:
                distance_km = _haversine_km(Point(*source_node), Point(*target_node))
                if distance_km <= MAX_FALLBACK_STRAIGHT_KM:
                    station_paths[f"{a}__{b}"] = [
                        [float(source_node[0]), float(source_node[1])],
                        [float(target_node[0]), float(target_node[1])],
                    ]
                else:
                    unresolved_pairs.append(f"{a}__{b}:gap>{MAX_FALLBACK_STRAIGHT_KM}km")

    network_segments = [
        [[float(u[0]), float(u[1])], [float(v[0]), float(v[1])]] for u, v in graph.edges()
    ]

    return {
        "meta": {
            "source": str(RAIL_GEOJSON_PATH),
            "simplify_tolerance_deg": SIMPLIFY_TOLERANCE_DEG,
            "raw_nodes": raw_graph.number_of_nodes(),
            "raw_edges": raw_graph.number_of_edges(),
            "component_nodes": graph.number_of_nodes(),
            "component_edges": graph.number_of_edges(),
            "unresolved_pairs": unresolved_pairs,
        },
        "network_segments": network_segments,
        "station_paths": station_paths,
    }


def main() -> None:
    optimized = build_optimized_tracks()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(optimized, f, ensure_ascii=False, indent=2)
        f.write("\n")

    meta = optimized["meta"]
    print(f"Saved: {OUTPUT_PATH}")
    print(f"raw graph: {meta['raw_nodes']} nodes / {meta['raw_edges']} edges")
    print(f"largest component: {meta['component_nodes']} nodes / {meta['component_edges']} edges")
    print(f"station paths: {len(optimized['station_paths'])}")
    if meta["unresolved_pairs"]:
        print("unresolved pairs:")
        for pair in meta["unresolved_pairs"]:
            print(f" - {pair}")


if __name__ == "__main__":
    main()
