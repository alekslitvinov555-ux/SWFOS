from __future__ import annotations

import json
from pathlib import Path

import folium
import networkx as nx
import streamlit as st
from streamlit_folium import st_folium

from src.graph_builder import build_graph
from src.routing import (
    RouteResult,
    ZERO_LOCO_WAIT_HOURS,
    calculate_route_cost,
    find_optimal_route,
    find_shortest_distance_route,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OPTIMIZED_TRACKS_PATH = DATA_DIR / "optimized_tracks.json"
ODESSA_SORT_STATION = "Odesa-Sortuvalna"
DERAILMENT_SCENARIO = "Derailment: Odesa-Skhidna to Kulyndorove"
DERAILMENT_SOURCE = "Odesa-Skhidna"
DERAILMENT_TARGET = "Kulyndorove"
DEMO_SCENARIOS = ("Normal Day", "Odesa Bottleneck", DERAILMENT_SCENARIO)


def _maybe_clear_streamlit_cache() -> None:
    with st.sidebar.expander("Data Refresh", expanded=False):
        if st.button("Reload graph from JSON (clear cache)", use_container_width=True):
            st.cache_data.clear()
            st.cache_resource.clear()
            st.success("Cache cleared. Reloading fresh graph data...")
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


def _format_time(hours: float) -> str:
    total_minutes = int(round(hours * 60))
    hrs, mins = divmod(total_minutes, 60)
    return f"{hrs} hrs {mins} mins"


def _format_currency_uah(value: float, signed: bool = False) -> str:
    if signed:
        return f"{value:+,.0f} ₴"
    return f"{value:,.0f} ₴"


def _apply_demo_scenario(graph: nx.DiGraph, scenario: str) -> None:
    for node, attrs in graph.nodes(data=True):
        capacity = float(attrs.get("capacity", 0.0))
        attrs["available_locomotives"] = 5
        attrs["current_load"] = min(attrs.get("current_load", 0.0), capacity * 0.35)

    for _u, _v, attrs in graph.edges(data=True):
        max_capacity = float(attrs.get("max_capacity", 0.0))
        attrs["current_flow"] = max_capacity * 0.25
        attrs["disruption_penalty_hours"] = 0.0

    if scenario == "Odesa Bottleneck" and ODESSA_SORT_STATION in graph.nodes:
        station_attrs = graph.nodes[ODESSA_SORT_STATION]
        station_capacity = float(station_attrs.get("capacity", 0.0))
        station_attrs["available_locomotives"] = 0
        station_attrs["current_load"] = station_capacity

        for u, v in graph.in_edges(ODESSA_SORT_STATION):
            graph[u][v]["current_flow"] = float(graph[u][v].get("max_capacity", 0.0))
        for u, v in graph.out_edges(ODESSA_SORT_STATION):
            graph[u][v]["current_flow"] = float(graph[u][v].get("max_capacity", 0.0))

    if scenario != DERAILMENT_SCENARIO:
        return

    for u, v in ((DERAILMENT_SOURCE, DERAILMENT_TARGET), (DERAILMENT_TARGET, DERAILMENT_SOURCE)):
        if graph.has_edge(u, v):
            graph[u][v]["disruption_penalty_hours"] = 48.0


def _build_event_log(
    graph: nx.DiGraph,
    scenario: str,
    smart_route: RouteResult,
    baseline_route: RouteResult,
    baseline_cost: float,
    money_saved: float,
) -> list[str]:
    logs: list[str] = []

    if scenario == "Odesa Bottleneck" and ODESSA_SORT_STATION in graph.nodes:
        odesa_attrs = graph.nodes[ODESSA_SORT_STATION]
        if int(odesa_attrs.get("available_locomotives", 5)) <= 0:
            logs.append(
                f"⚠️ {ODESSA_SORT_STATION} has 0 locomotives (+{int(ZERO_LOCO_WAIT_HOURS)}h penalty)."
            )
        if _utilization(odesa_attrs) >= 1.0:
            logs.append(f"🚧 {ODESSA_SORT_STATION} is at 100% station utilization.")
    if scenario == DERAILMENT_SCENARIO and graph.has_edge(DERAILMENT_SOURCE, DERAILMENT_TARGET):
        penalty_hours = int(float(graph[DERAILMENT_SOURCE][DERAILMENT_TARGET].get("disruption_penalty_hours", 0.0)))
        if penalty_hours > 0:
            logs.append(
                f"🛑 Derailment on {DERAILMENT_SOURCE} ↔ {DERAILMENT_TARGET}: +{penalty_hours}h disruption penalty."
            )

    if smart_route.path != baseline_route.path:
        logs.append(
            f"🤖 AI rerouted from {' ➜ '.join(baseline_route.path)} to {' ➜ '.join(smart_route.path)}."
        )
    else:
        logs.append("ℹ️ AI kept the baseline path because no cheaper detour was available.")

    logs.append(
        f"📊 Baseline cost: {_format_currency_uah(baseline_cost)} vs smart cost: {_format_currency_uah(smart_route.total_cost)}."
    )
    logs.append(f"💰 Economic impact: {_format_currency_uah(money_saved, signed=True)}.")

    return logs


def _apply_sidebar_station_load_controls(graph) -> None:
    with st.sidebar.expander("Simulation Controls: Station Loads", expanded=False):
        for station in sorted(graph.nodes):
            attrs = graph.nodes[station]
            capacity = int(float(attrs.get("capacity", 0)))
            current_load = int(float(attrs.get("current_load", 0)))

            updated_load = st.slider(
                label=f"{station} current_load",
                min_value=0,
                max_value=capacity,
                value=min(current_load, capacity),
                step=1,
            )
            graph.nodes[station]["current_load"] = float(updated_load)


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
    st.title("Dispatcher Dashboard")
    st.caption("Smart Wagon Flow Optimization • Ukrainian Railway MVP")
    st.divider()

    _maybe_clear_streamlit_cache()

    graph = build_graph(DATA_DIR / "stations.json", DATA_DIR / "edges.json")
    with st.sidebar.expander("Scenario Settings", expanded=True):
        demo_scenario = st.selectbox("Demo Scenarios", DEMO_SCENARIOS, index=0)
    _apply_demo_scenario(graph, demo_scenario)
    _apply_sidebar_station_load_controls(graph)

    stations = sorted(graph.nodes)
    with st.container():
        st.subheader("Route Settings")
        selector_col1, selector_col2 = st.columns(2)

        with selector_col1:
            source = st.selectbox("Source station", stations, index=0)
        with selector_col2:
            default_target = stations.index("Izmail") if "Izmail" in stations else len(stations) - 1
            target = st.selectbox("Target station", stations, index=default_target)

    if source == target:
        st.warning("Please choose different source and target stations.")
        return

    try:
        result = find_optimal_route(graph=graph, source=source, target=target)
        baseline_route = find_shortest_distance_route(graph=graph, source=source, target=target)
    except nx.NetworkXNoPath:
        st.warning("No feasible route was found for the selected stations.")
        return

    baseline_cost = calculate_route_cost(graph, baseline_route.path)
    money_saved = baseline_cost - result.total_cost

    st.subheader("KPI Overview")
    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
    with kpi_col1:
        st.metric("Total Route Time", _format_time(result.total_time_hours))
    with kpi_col2:
        st.metric("Smart Route Cost", _format_currency_uah(result.total_cost))
    with kpi_col3:
        st.metric("Baseline Route Cost", _format_currency_uah(baseline_cost))
    with kpi_col4:
        st.metric(
            "Money Saved",
            _format_currency_uah(abs(money_saved)),
            delta=_format_currency_uah(money_saved, signed=True),
            delta_color="normal",
        )

    st.divider()
    route_col, baseline_col = st.columns(2)
    with route_col:
        st.subheader("Optimal Route")
        st.write(" ➜ ".join(result.path))
    with baseline_col:
        st.subheader("Baseline Route")
        st.caption("Shortest-Distance Benchmark")
        st.write(" ➜ ".join(baseline_route.path))

    event_log_items = _build_event_log(
        graph=graph,
        scenario=demo_scenario,
        smart_route=result,
        baseline_route=baseline_route,
        baseline_cost=baseline_cost,
        money_saved=money_saved,
    )

    route_map = _build_map(graph, result.path)
    map_tab, log_tab = st.tabs(["Route Map", "Enterprise Event Log"])
    with map_tab:
        st_folium(
            route_map,
            width=800,
            height=620,
            returned_objects=[],
            use_container_width=True,
        )
    with log_tab:
        for item in event_log_items:
            st.write(f"- {item}")


if __name__ == "__main__":
    main()
