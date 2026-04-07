from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import requests

OVERPASS_URL = "http://overpass-api.de/api/interpreter"
PROJECT_ROOT = Path(__file__).resolve().parent
EDGES_PATH = PROJECT_ROOT / "data" / "edges.json"

# Corridors we want to enrich with high-fidelity geometry.
# The key is unordered because edges are bidirectional in data.
TARGET_CORRIDORS = {
    frozenset({"Odesa-Sortuvalna", "Kolosivka"}): {
        "label": "Odesa-Sortuvalna <-> Kolosivka",
        "bbox": (46.45, 30.70, 47.35, 31.10),  # south, west, north, east
        "start": (46.5300, 30.7700),
        "end": (47.2900, 31.0200),
    },
    frozenset({"Kolosivka", "Mykolaiv"}): {
        "label": "Kolosivka <-> Mykolaiv",
        "bbox": (46.90, 30.95, 47.35, 32.10),
        "start": (47.2900, 31.0200),
        "end": (46.9750, 31.9946),
    },
    frozenset({"Odesa-Sortuvalna", "Bilhorod-Dnistrovskyi"}): {
        "label": "Odesa-Sortuvalna <-> Bilhorod-Dnistrovskyi",
        "bbox": (46.15, 30.28, 46.70, 30.90),
        "start": (46.5300, 30.7700),
        "end": (46.1900, 30.3500),
    },
}

# Offline-safe dense fallback control polylines (geographically curved).
FALLBACK_CONTROL_POINTS = {
    frozenset({"Odesa-Sortuvalna", "Kolosivka"}): [
        [46.5300, 30.7700],
        [46.5600, 30.7750],
        [46.6000, 30.7850],
        [46.6500, 30.8000],
        [46.7000, 30.8150],
        [46.7600, 30.8300],
        [46.8200, 30.8450],
        [46.8800, 30.8600],
        [46.9500, 30.8750],
        [47.0200, 30.8900],
        [47.0900, 30.9100],
        [47.1500, 30.9400],
        [47.2100, 30.9700],
        [47.2500, 30.9950],
        [47.2900, 31.0200],
    ],
    frozenset({"Kolosivka", "Mykolaiv"}): [
        [47.2900, 31.0200],
        [47.2700, 31.0600],
        [47.2450, 31.1100],
        [47.2200, 31.1700],
        [47.2000, 31.2400],
        [47.1850, 31.3200],
        [47.1700, 31.4000],
        [47.1500, 31.4900],
        [47.1300, 31.5700],
        [47.1100, 31.6500],
        [47.0900, 31.7200],
        [47.0600, 31.7900],
        [47.0300, 31.8600],
        [47.0000, 31.9300],
        [46.9750, 31.9946],
    ],
    frozenset({"Odesa-Sortuvalna", "Bilhorod-Dnistrovskyi"}): [
        [46.5300, 30.7700],
        [46.5450, 30.7900],
        [46.5620, 30.8120],
        [46.5800, 30.8280],
        [46.5980, 30.8230],
        [46.6140, 30.8050],
        [46.6230, 30.7780],
        [46.6220, 30.7440],
        [46.6150, 30.7110],
        [46.6020, 30.6810],
        [46.5840, 30.6510],
        [46.5600, 30.6210],
        [46.5330, 30.5920],
        [46.5030, 30.5630],
        [46.4700, 30.5350],
        [46.4350, 30.5080],
        [46.3980, 30.4840],
        [46.3600, 30.4600],
        [46.3220, 30.4380],
        [46.2850, 30.4180],
        [46.2480, 30.3980],
        [46.2200, 30.3720],
        [46.1900, 30.3500],
    ],
}


def _distance_sq(a: list[float], b: list[float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _dedupe_sequential(points: Iterable[list[float]]) -> list[list[float]]:
    deduped: list[list[float]] = []
    for p in points:
        if not deduped or _distance_sq(deduped[-1], p) > 1e-10:
            deduped.append(p)
    return deduped


def _densify_polyline(points: list[list[float]], target_count: int = 50) -> list[list[float]]:
    if len(points) >= target_count:
        return points
    if len(points) < 2:
        return points

    extra_needed = target_count - len(points)
    segments = len(points) - 1
    base_extra = extra_needed // segments
    remainder = extra_needed % segments

    dense: list[list[float]] = []
    for i in range(segments):
        p1 = points[i]
        p2 = points[i + 1]
        dense.append([round(p1[0], 6), round(p1[1], 6)])

        inserts = base_extra + (1 if i < remainder else 0)
        for k in range(1, inserts + 1):
            t = k / (inserts + 1)
            lat = p1[0] + (p2[0] - p1[0]) * t
            lon = p1[1] + (p2[1] - p1[1]) * t
            dense.append([round(lat, 6), round(lon, 6)])

    dense.append([round(points[-1][0], 6), round(points[-1][1], 6)])
    return dense


def _query_overpass_for_bbox(bbox: tuple[float, float, float, float]) -> list[list[float]]:
    south, west, north, east = bbox
    query = f"""
[out:json][timeout:60];
(
  way[\"railway\"=\"rail\"]({south},{west},{north},{east});
);
out geom;
""".strip()

    response = requests.post(OVERPASS_URL, data={"data": query}, timeout=60)
    response.raise_for_status()

    payload = response.json()
    points: list[list[float]] = []

    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue
        geometry = element.get("geometry", [])
        for p in geometry:
            if "lat" in p and "lon" in p:
                points.append([float(p["lat"]), float(p["lon"])])

    return _dedupe_sequential(points)


def _order_points_by_projection(
    points: list[list[float]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[list[float]]:
    if not points:
        return []

    sy, sx = start
    ey, ex = end
    dy = ey - sy
    dx = ex - sx
    norm = dy * dy + dx * dx
    if norm == 0:
        return points

    def projection_t(p: list[float]) -> float:
        return ((p[0] - sy) * dy + (p[1] - sx) * dx) / norm

    return sorted(points, key=projection_t)


def _live_or_fallback_waypoints(
    corridor_key: frozenset[str],
    mode: str,
) -> tuple[list[list[float]], str]:
    corridor = TARGET_CORRIDORS[corridor_key]

    if mode in {"auto", "live"}:
        try:
            live_points = _query_overpass_for_bbox(corridor["bbox"])
            live_points = _order_points_by_projection(live_points, corridor["start"], corridor["end"])
            live_points = _densify_polyline(live_points, target_count=50)

            if len(live_points) >= 50:
                return live_points, "overpass"
        except Exception as exc:  # noqa: BLE001 - explicit fallback strategy
            print(f"[WARN] Overpass failed for {corridor['label']}: {exc}")
            if mode == "live":
                raise

    fallback_points = FALLBACK_CONTROL_POINTS[corridor_key]
    fallback_points = _densify_polyline(fallback_points, target_count=50)
    return fallback_points, "fallback"


def update_edges_json(mode: str = "auto") -> None:
    with EDGES_PATH.open("r", encoding="utf-8") as f:
        edges = json.load(f)

    corridor_waypoints: dict[frozenset[str], list[list[float]]] = {}
    corridor_sources: dict[frozenset[str], str] = {}

    for corridor_key in TARGET_CORRIDORS:
        points, source = _live_or_fallback_waypoints(corridor_key, mode=mode)
        corridor_waypoints[corridor_key] = points
        corridor_sources[corridor_key] = source

    updated = 0
    for edge in edges:
        key = frozenset({edge["source"], edge["target"]})
        if key in corridor_waypoints:
            edge["waypoints"] = corridor_waypoints[key]
            updated += 1

    with EDGES_PATH.open("w", encoding="utf-8") as f:
        json.dump(edges, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Updated edges: {updated}")
    for key, info in TARGET_CORRIDORS.items():
        points_count = len(corridor_waypoints[key])
        source = corridor_sources[key]
        print(f" - {info['label']}: {points_count} points ({source})")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and enrich railway edge waypoints from Overpass API.")
    parser.add_argument(
        "--mode",
        choices=["auto", "live", "fallback"],
        default="auto",
        help="auto: try Overpass then fallback, live: Overpass only, fallback: no network",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    update_edges_json(mode=args.mode)


if __name__ == "__main__":
    main()
