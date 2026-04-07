from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import sleep

import requests

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]
DEFAULT_OUTPUT = Path("data/railways_southern_ukraine.geojson")
DEFAULT_EDGES = Path("data/edges.json")
DEFAULT_STATIONS = Path("data/stations.json")


def build_query(south: float, west: float, north: float, east: float) -> str:
    return f"""
[out:json][timeout:120];
(
  way["railway"~"rail|light_rail|subway|narrow_gauge"]({south},{west},{north},{east});
);
out tags geom;
""".strip()


def overpass_to_geojson(payload: dict) -> dict:
    features: list[dict] = []

    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue

        geometry = element.get("geometry", [])
        if len(geometry) < 2:
            continue

        coordinates = [[pt["lon"], pt["lat"]] for pt in geometry if "lat" in pt and "lon" in pt]
        if len(coordinates) < 2:
            continue

        tags = element.get("tags", {})
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_way_id": element.get("id"),
                    "railway": tags.get("railway"),
                    "usage": tags.get("usage"),
                    "service": tags.get("service"),
                    "name": tags.get("name"),
                    "electrified": tags.get("electrified"),
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "name": "southern_ukraine_rail_network",
        "features": features,
    }


def _build_fallback_geojson_from_edges(
    edges_path: Path,
    stations_path: Path,
) -> dict:
    with edges_path.open("r", encoding="utf-8") as f:
        edges = json.load(f)
    with stations_path.open("r", encoding="utf-8") as f:
        stations = json.load(f)

    station_map = {s["name"]: [float(s["lat"]), float(s["lon"])] for s in stations}
    features: list[dict] = []

    for idx, edge in enumerate(edges):
        src = edge["source"]
        dst = edge["target"]
        src_coord = station_map.get(src)
        dst_coord = station_map.get(dst)
        if not src_coord or not dst_coord:
            continue

        waypoints = [[float(p[0]), float(p[1])] for p in edge.get("waypoints", [])]
        line_latlon = [src_coord] + waypoints + [dst_coord]
        coordinates = [[p[1], p[0]] for p in line_latlon]

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_way_id": f"fallback_{idx}",
                    "railway": "rail",
                    "name": f"{src} -> {dst}",
                    "source": "fallback-from-edges",
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "name": "southern_ukraine_rail_network_fallback",
        "features": features,
    }


def fetch_rail_network_geojson(
    south: float,
    west: float,
    north: float,
    east: float,
    output: Path,
    mode: str,
) -> None:
    query = build_query(south, west, north, east)

    geojson: dict | None = None
    last_error: Exception | None = None

    if mode in {"auto", "live"}:
        for endpoint in OVERPASS_ENDPOINTS:
            for attempt in range(1, 3):
                try:
                    response = requests.post(endpoint, data={"data": query}, timeout=180)
                    response.raise_for_status()
                    geojson = overpass_to_geojson(response.json())
                    print(f"Fetched from {endpoint} (attempt {attempt})")
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    print(f"[WARN] {endpoint} attempt {attempt} failed: {exc}")
                    sleep(1.2)

            if geojson and geojson.get("features"):
                break

    if (not geojson or not geojson.get("features")) and mode in {"auto", "fallback"}:
        print("[INFO] Using fallback GeoJSON generated from local edges/stations.")
        geojson = _build_fallback_geojson_from_edges(DEFAULT_EDGES, DEFAULT_STATIONS)

    if not geojson or not geojson.get("features"):
        raise RuntimeError(f"Failed to build rail GeoJSON. Last error: {last_error}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Saved GeoJSON: {output}")
    print(f"Features: {len(geojson['features'])}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Southern Ukraine railway network from OSM/Overpass as GeoJSON."
    )
    parser.add_argument("--south", type=float, default=45.1)
    parser.add_argument("--west", type=float, default=28.4)
    parser.add_argument("--north", type=float, default=48.9)
    parser.add_argument("--east", type=float, default=33.6)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode",
        choices=["auto", "live", "fallback"],
        default="auto",
        help="auto: try Overpass then fallback; live: Overpass only; fallback: local edges/stations only",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fetch_rail_network_geojson(
        south=args.south,
        west=args.west,
        north=args.north,
        east=args.east,
        output=args.output,
        mode=args.mode,
    )


if __name__ == "__main__":
    main()
