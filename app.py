from __future__ import annotations

import json
import math
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
DERAILMENT_SCENARIO = "Derailment Near Odesa"
DERAILMENT_PRIMARY_SOURCE = "Kolosivka"
DERAILMENT_TIME_PENALTY_MULTIPLIER = 4
DEMO_SCENARIOS = ("Normal Day", "Odesa Bottleneck", DERAILMENT_SCENARIO)
# Approximate linear click snap radius in degrees; compared using latitude-adjusted squared distance.
MAX_STATION_CLICK_DISTANCE_DEGREES = 0.03
# Mock station analytics defaults used until live dispatcher telemetry is integrated.
DEFAULT_STATION_TRACK_CAPACITY = 10
MOCK_WAITING_TRAINS_RATIO = 0.15
MOCK_MIN_WAITING_TRAINS = 1
MOCK_BASE_DELAY_HOURS = 4
MOCK_UTILIZATION_DELAY_MULTIPLIER = 3
# Keeps longitude weighting from collapsing near poles (caps distortion to ~10x).
MIN_LONGITUDE_SCALE = 0.1
NETWORK_TRACK_STYLE = {"color": "#666666", "weight": 1.8, "opacity": 0.35}
ROUTE_GLOW_STYLE = {"color": "#34f5ff", "weight": 10, "opacity": 0.25}
ROUTE_CORE_STYLE = {"color": "#20ffd5", "weight": 6, "opacity": 0.9}


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


def _segment_coordinates(
    graph: nx.DiGraph,
    u: str,
    v: str,
    optimized_tracks: dict | None = None,
    prefer_optimized: bool = False,
) -> list[list[float]]:
    start_node_coords = [float(graph.nodes[u]["lat"]), float(graph.nodes[u]["lon"])]
    end_node_coords = [float(graph.nodes[v]["lat"]), float(graph.nodes[v]["lon"])]
    if prefer_optimized:
        segment_coords = _segment_from_optimized(optimized_tracks, u, v)
        if segment_coords:
            return segment_coords
    edge_data = _extract_edge_data(graph, u, v)
    edge_wps = edge_data.get("waypoints", [])
    return [start_node_coords] + edge_wps + [end_node_coords]


def _utilization(attrs: dict) -> float:
    capacity = float(attrs.get("capacity", 0))
    current_load = float(attrs.get("current_load", 0))
    return current_load / capacity if capacity > 0 else 1.0


def _station_congestion_style(attrs: dict) -> tuple[str, str]:
    utilization = _utilization(attrs)
    available_locomotives = int(attrs.get("available_locomotives", 0))
    if available_locomotives <= 0 or utilization >= 0.95:
        return "#ef4444", "Bottleneck"
    if utilization >= 0.7:
        return "#facc15", "Busy"
    return "#22c55e", "Free"


def _resolve_clicked_station(graph: nx.DiGraph, click_data: dict | None) -> str | None:
    if not click_data:
        return None

    clicked_lat = click_data.get("lat")
    clicked_lon = click_data.get("lng", click_data.get("lon"))
    if clicked_lat is None or clicked_lon is None:
        return None

    nearest_station: str | None = None
    nearest_distance = float("inf")

    def _distance_sq_scaled(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        mean_lat_rad = math.radians((lat1 + lat2) / 2.0)
        # Guardrail keeps longitudinal scaling stable at high latitudes (avoids overly permissive clicks).
        lon_scale = max(MIN_LONGITUDE_SCALE, abs(math.cos(mean_lat_rad)))
        lat_delta = lat1 - lat2
        lon_delta = (lon1 - lon2) * lon_scale
        return lat_delta**2 + lon_delta**2

    for station, attrs in graph.nodes(data=True):
        lat = float(attrs["lat"])
        lon = float(attrs["lon"])
        distance = _distance_sq_scaled(lat, lon, float(clicked_lat), float(clicked_lon))
        if distance < nearest_distance:
            nearest_distance = distance
            nearest_station = station

    max_click_distance_sq = MAX_STATION_CLICK_DISTANCE_DEGREES**2
    if nearest_distance > max_click_distance_sq:
        return None
    return nearest_station


def _render_station_analytics(graph: nx.DiGraph, station: str) -> None:
    attrs = graph.nodes[station]
    utilization = _utilization(attrs)
    available_locomotives = int(attrs.get("available_locomotives", 0))

    max_tracks = int(attrs.get("max_tracks", DEFAULT_STATION_TRACK_CAPACITY))
    used_tracks = min(max_tracks, max(0, int(round(utilization * max_tracks))))
    available_tracks = max_tracks - used_tracks
    current_load = float(attrs.get("current_load", 0))
    trains_waiting = int(round(current_load * MOCK_WAITING_TRAINS_RATIO))
    if current_load > 0 and trains_waiting == 0:
        trains_waiting = max(MOCK_MIN_WAITING_TRAINS, trains_waiting)
    estimated_delay_hours = _calculate_estimated_delay_hours(utilization)
    status_color, status_label = _station_congestion_style(attrs)

    with st.sidebar.expander("Station Analytics Dashboard", expanded=True):
        st.markdown(f"### {station}")
        st.markdown(
            f"**Congestion Status:** <span style='color:{status_color};font-weight:600'>{status_label}</span>",
            unsafe_allow_html=True,
        )
        stat_col1, stat_col2 = st.columns(2)
        with stat_col1:
            st.metric("Current Trains Waiting", trains_waiting)
            st.metric("Available Locomotives", available_locomotives)
        with stat_col2:
            st.metric("Available Tracks", f"{available_tracks}/{max_tracks}")
            st.metric("Avg Delay", f"{estimated_delay_hours} hours")


def _calculate_estimated_delay_hours(utilization: float) -> int:
    return MOCK_BASE_DELAY_HOURS + int(round(utilization * MOCK_UTILIZATION_DELAY_MULTIPLIER))


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

    # Reset all edge-level simulation artifacts before applying a specific demo scenario.
    for _u, _v, attrs in graph.edges(data=True):
        max_capacity = float(attrs.get("max_capacity", 0.0))
        attrs["current_flow"] = max_capacity * 0.25
        # Scenario flags are synthetic and must not leak across reruns.
        attrs.pop("incident", None)

    if scenario == DERAILMENT_SCENARIO and ODESSA_SORT_STATION in graph.nodes:
        incoming_edges = sorted(graph.in_edges(ODESSA_SORT_STATION))
        preferred_edge = (DERAILMENT_PRIMARY_SOURCE, ODESSA_SORT_STATION)
        if preferred_edge in incoming_edges:
            derailment_edge = preferred_edge
        elif incoming_edges:
            derailment_edge = incoming_edges[0]
        else:
            derailment_edge = None
        if derailment_edge:
            derail_u, derail_v = derailment_edge
            derail_edge = graph[derail_u][derail_v]
            derail_edge["current_flow"] = float(derail_edge.get("max_capacity", 0.0))
            derail_edge["base_time"] = (
                float(derail_edge.get("base_time", 0.0)) * DERAILMENT_TIME_PENALTY_MULTIPLIER
            )
            derail_edge["incident"] = "derailment"
        return

    if scenario != "Odesa Bottleneck" or ODESSA_SORT_STATION not in graph.nodes:
        return

    station_attrs = graph.nodes[ODESSA_SORT_STATION]
    station_capacity = float(station_attrs.get("capacity", 0.0))
    station_attrs["available_locomotives"] = 0
    station_attrs["current_load"] = station_capacity

    for u, v in graph.in_edges(ODESSA_SORT_STATION):
        graph[u][v]["current_flow"] = float(graph[u][v].get("max_capacity", 0.0))
    for u, v in graph.out_edges(ODESSA_SORT_STATION):
        graph[u][v]["current_flow"] = float(graph[u][v].get("max_capacity", 0.0))


def _build_event_log(
    graph: nx.DiGraph,
    scenario: str,
    smart_route: RouteResult,
    baseline_route: RouteResult,
    baseline_cost: float,
    money_saved: float,
) -> list[str]:
    logs: list[str] = []

    if scenario == DERAILMENT_SCENARIO and ODESSA_SORT_STATION in graph.nodes:
        for u, v in sorted(graph.in_edges(ODESSA_SORT_STATION)):
            edge_attrs = graph[u][v]
            if edge_attrs.get("incident") == "derailment":
                logs.append(f"🚨 Derailment reported on {u} ➜ {v}; traffic rerouted around the incident.")
                break

    if scenario == "Odesa Bottleneck" and ODESSA_SORT_STATION in graph.nodes:
        odesa_attrs = graph.nodes[ODESSA_SORT_STATION]
        if int(odesa_attrs.get("available_locomotives", 5)) <= 0:
            logs.append(
                f"⚠️ {ODESSA_SORT_STATION} has 0 locomotives (+{int(ZERO_LOCO_WAIT_HOURS)}h penalty)."
            )
        if _utilization(odesa_attrs) >= 1.0:
            logs.append(f"🚧 {ODESSA_SORT_STATION} is at 100% station utilization.")

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

    def _draw_network_segment(segment_coords: list[list[float]], group: folium.FeatureGroup) -> None:
        folium.PolyLine(locations=segment_coords, **NETWORK_TRACK_STYLE).add_to(group)

    if optimized_tracks:
        network_group = folium.FeatureGroup(name="Optimized Rail Network", overlay=True, control=True)

        for segment in optimized_tracks.get("network_segments", []):
            _draw_network_segment(segment, network_group)

        network_group.add_to(m)
    else:
        network_group = folium.FeatureGroup(name="Rail Network", overlay=True, control=True)
        for u, v in graph.edges():
            segment_coords = _segment_coordinates(graph, u, v)
            _draw_network_segment(segment_coords, network_group)
        network_group.add_to(m)

    for node, attrs in graph.nodes(data=True):
        utilization = _utilization(attrs)
        color, congestion_label = _station_congestion_style(attrs)

        folium.CircleMarker(
            location=(attrs["lat"], attrs["lon"]),
            radius=9,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.8,
            tooltip=(
                f"{node} | {congestion_label} | Load: {attrs['current_load']}/"
                f"{attrs['capacity']} ({utilization:.1%})"
            ),
            popup=(
                f"{node}<br>"
                f"Status: {congestion_label}<br>"
                f"Capacity: {attrs['capacity']}<br>"
                f"Current load: {attrs['current_load']}<br>"
                f"Utilization: {utilization:.1%}<br>"
                f"Available locomotives: {int(attrs.get('available_locomotives', 0))}"
            ),
        ).add_to(m)

    for i in range(len(route) - 1):
        u = route[i]
        v = route[i + 1]

        segment_coords = _segment_coordinates(graph, u, v, optimized_tracks, prefer_optimized=True)

        folium.PolyLine(
            locations=segment_coords,
            **ROUTE_GLOW_STYLE,
            tooltip=f"Optimized route: {u} -> {v}",
        ).add_to(m)

        folium.PolyLine(
            locations=segment_coords,
            **ROUTE_CORE_STYLE,
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
        map_state = st_folium(
            route_map,
            width=800,
            height=620,
            returned_objects=["last_object_clicked"],
            use_container_width=True,
        )
        clicked_station = _resolve_clicked_station(graph, (map_state or {}).get("last_object_clicked"))
        if clicked_station:
            st.session_state["selected_station"] = clicked_station
            st.info(f"Selected station: {clicked_station} — see Station Analytics Dashboard in sidebar.")
    with log_tab:
        for item in event_log_items:
            st.write(f"- {item}")

    selected_station = st.session_state.get("selected_station")
    if selected_station in graph.nodes:
        _render_station_analytics(graph, selected_station)


if __name__ == "__main__":
    main()
