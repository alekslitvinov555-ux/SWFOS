from __future__ import annotations

import json
from pathlib import Path

import folium
import networkx as nx
import streamlit as st
from streamlit_folium import st_folium

from src.graph_builder import build_graph
from src.routing import RouteResult, find_optimal_route, make_edge_cost_function


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OPTIMIZED_TRACKS_PATH = DATA_DIR / "optimized_tracks.json"
MOCK_SAVED_UAH = 12 * 50 * 1000  # 600,000 UAH
FACTOR_8_DELAY_HOURS = 10.0
SORTING_STATIONS = {"Odesa-Sortuvalna", "Znamianka"}


def _maybe_clear_streamlit_cache() -> None:
    st.sidebar.subheader("Data Refresh")
    if st.sidebar.button("Reload graph from JSON (clear cache)"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.sidebar.success("Cache cleared. Reloading fresh graph data...")
        st.rerun()


def _extract_edge_data(graph, u: str, v: str) -> dict:
    edge_data = graph.get_edge_data(u, v)
    if edge_data is None:
        return {}

    # DiGraph: {'base_time': ..., 'waypoints': ...}
    # MultiDiGraph: {0: {'base_time': ..., 'waypoints': ...}, ...}
    if isinstance(edge_data, dict) and "waypoints" not in edge_data and len(edge_data) > 0:
        first_key = next(iter(edge_data))
        maybe_nested = edge_data.get(first_key)
        if isinstance(maybe_nested, dict):
            return maybe_nested

    return edge_data if isinstance(edge_data, dict) else {}


@st.cache_data(show_spinner=False)
def _load_optimized_tracks(path: str) -> dict | None:
    file_path = Path(path)
    if not file_path.exists():
        return None

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _segment_from_optimized(
    optimized_tracks: dict | None,
    source: str,
    target: str,
) -> list[list[float]] | None:
    if not optimized_tracks:
        return None

    paths = optimized_tracks.get("station_paths", {})
    direct_key = f"{source}__{target}"
    reverse_key = f"{target}__{source}"

    if direct_key in paths:
        return paths[direct_key]
    if reverse_key in paths:
        return list(reversed(paths[reverse_key]))

    return None


def _utilization(attrs: dict) -> float:
    capacity = float(attrs.get("capacity", 0))
    current_load = float(attrs.get("current_load", 0))
    return current_load / capacity if capacity > 0 else 1.0


def _find_bottlenecks(graph) -> list[str]:
    return [node for node, attrs in graph.nodes(data=True) if _utilization(attrs) > 0.9]


def _apply_sidebar_station_load_controls(graph) -> None:
    st.sidebar.header("Simulation Controls: Station Loads")

    for station in sorted(graph.nodes):
        attrs = graph.nodes[station]
        capacity = int(float(attrs.get("capacity", 0)))
        current_load = int(float(attrs.get("current_load", 0)))

        updated_load = st.sidebar.slider(
            label=f"{station} current_load",
            min_value=0,
            max_value=capacity,
            value=min(current_load, capacity),
            step=1,
        )
        graph.nodes[station]["current_load"] = float(updated_load)


def _route_with_factor_8(graph, source: str, target: str, factor_8_enabled: bool) -> RouteResult:
    if not factor_8_enabled:
        return find_optimal_route(graph, source=source, target=target)

    present_sorting_stations = {station for station in SORTING_STATIONS if station in graph.nodes}
    base_weight = make_edge_cost_function(graph)

    def scenario_weight(u: str, v: str, edge_data: dict) -> float:
        cost = base_weight(u, v, edge_data)
        if v in present_sorting_stations:
            cost += FACTOR_8_DELAY_HOURS
        return cost

    path = nx.dijkstra_path(graph, source=source, target=target, weight=scenario_weight)
    segment_costs = [scenario_weight(u, v, graph[u][v]) for u, v in zip(path[:-1], path[1:])]

    return RouteResult(path=path, total_cost=sum(segment_costs), segment_costs=segment_costs)


def _was_rerouted(
    graph,
    source: str,
    target: str,
    optimized_path: list[str],
    bottlenecks: list[str],
) -> bool:
    if not bottlenecks:
        return False

    baseline_path = nx.dijkstra_path(
        graph,
        source=source,
        target=target,
        weight=lambda _u, _v, edge_data: float(edge_data.get("base_time", 1.0)),
    )

    baseline_bottlenecks = set(baseline_path).intersection(bottlenecks)
    optimized_bottlenecks = set(optimized_path).intersection(bottlenecks)

    return bool(baseline_bottlenecks) and len(optimized_bottlenecks) < len(baseline_bottlenecks)


def _build_map(graph, route: list[str]) -> folium.Map:
    coordinates = [(graph.nodes[node]["lat"], graph.nodes[node]["lon"]) for node in route]

    center_lat = sum(lat for lat, _ in coordinates) / len(coordinates)
    center_lon = sum(lon for _, lon in coordinates) / len(coordinates)

    m = folium.Map(
        location=(center_lat, center_lon),
        zoom_start=7,
        control_scale=True,
        tiles="CartoDB positron",
    )

    folium.TileLayer(
        tiles="https://{s}.tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png",
        attr='Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | Map style: &copy; <a href="https://www.OpenRailwayMap.org">OpenRailwayMap</a>',
        name="OpenRailwayMap",
        overlay=True,
        control=True,
    ).add_to(m)

    optimized_tracks = _load_optimized_tracks(str(OPTIMIZED_TRACKS_PATH))

    if optimized_tracks:
        network_group = folium.FeatureGroup(name="Optimized Rail Network", overlay=True, control=True)

        for segment in optimized_tracks.get("network_segments", []):
            folium.PolyLine(
                locations=segment,
                color="#5b5b5b",
                weight=2,
                opacity=0.55,
            ).add_to(network_group)

        network_group.add_to(m)
    else:
        st.warning(
            "`data/optimized_tracks.json` not found. Run `build_optimized_tracks.py` first for fast GIS mode."
        )

    for node, attrs in graph.nodes(data=True):
        utilization = _utilization(attrs)
        color = "red" if utilization > 0.9 else "green"

        folium.CircleMarker(
            location=(attrs["lat"], attrs["lon"]),
            radius=9,
            color=color,
            fill=True,
            fill_opacity=0.8,
            tooltip=f"{node} | Load: {attrs['current_load']}/{attrs['capacity']} ({utilization:.1%})",
            popup=(
                f"{node}<br>"
                f"Capacity: {attrs['capacity']}<br>"
                f"Current load: {attrs['current_load']}<br>"
                f"Utilization: {utilization:.1%}"
            ),
        ).add_to(m)

    for i in range(len(route) - 1):
        u = route[i]
        v = route[i + 1]

        start_node_coords = [float(graph.nodes[u]["lat"]), float(graph.nodes[u]["lon"])]
        end_node_coords = [float(graph.nodes[v]["lat"]), float(graph.nodes[v]["lon"])]
        segment_coords = _segment_from_optimized(optimized_tracks, u, v)
        if not segment_coords:
            edge_data = _extract_edge_data(graph, u, v)
            edge_wps = edge_data.get("waypoints", [])
            segment_coords = [start_node_coords] + edge_wps + [end_node_coords]

        st.sidebar.write(f"Segment {u} -> {v}: drawn using {len(segment_coords)} coordinates.")

        folium.PolyLine(
            locations=segment_coords,
            color="blue",
            weight=5,
            opacity=0.9,
            tooltip=f"Optimized route: {u} -> {v}",
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)

    return m


def main() -> None:
    st.set_page_config(page_title="Dispatcher Dashboard | Smart Wagon Flow", layout="wide")
    st.title("🚦 Dispatcher Dashboard — Smart Wagon Flow Optimization")
    st.caption("Ukrainian Railway MVP: congestion-aware wagon flow routing")

    _maybe_clear_streamlit_cache()
    st.sidebar.divider()

    graph = build_graph(DATA_DIR / "stations.json", DATA_DIR / "edges.json")

    _apply_sidebar_station_load_controls(graph)
    st.sidebar.divider()
    factor_8_enabled = st.sidebar.checkbox(
        "Simulate Factor #8 (Sorting)",
        value=False,
        help="Adds 10 hours to routes that pass through Odesa-Sortuvalna or Znamianka.",
    )

    bottlenecks = _find_bottlenecks(graph)

    if bottlenecks:
        st.error(
            "\n".join(
                [f"Bottleneck detected at {station}! Redirecting traffic." for station in bottlenecks]
            )
        )

    stations = sorted(graph.nodes)
    col1, col2 = st.columns(2)

    with col1:
        source = st.selectbox("Source station", stations, index=0)
    with col2:
        default_target = stations.index("Izmail") if "Izmail" in stations else len(stations) - 1
        target = st.selectbox("Target station", stations, index=default_target)

    if source == target:
        st.warning("Please choose different source and target stations.")
        return

    try:
        result = _route_with_factor_8(
            graph=graph,
            source=source,
            target=target,
            factor_8_enabled=factor_8_enabled,
        )
    except nx.NetworkXNoPath:
        st.warning("No feasible route was found for the selected stations.")
        return

    rerouted = _was_rerouted(
        graph=graph,
        source=source,
        target=target,
        optimized_path=result.path,
        bottlenecks=bottlenecks,
    )
    money_saved = MOCK_SAVED_UAH if rerouted else 0

    kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
    with kpi_col1:
        st.metric("Route Status", "Rerouted" if rerouted else "Normal")
    with kpi_col2:
        st.metric("Estimated Time", f"{result.total_cost:.2f} h")
    with kpi_col3:
        st.metric("Money Saved", f"{money_saved:,.0f} UAH")

    st.subheader("Optimal Route")
    st.write(" ➜ ".join(result.path))

    if rerouted:
        st.warning("Dynamic rerouting active: route avoids a >90% congested bottleneck station.")

    route_map = _build_map(graph, result.path)
    st.subheader("Route Map")
    st_folium(
        route_map,
        width=800,
        height=620,
        returned_objects=[],
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
