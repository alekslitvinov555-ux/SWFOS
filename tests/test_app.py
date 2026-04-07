from __future__ import annotations

import unittest

import networkx as nx

from app import (
    ODESSA_SORT_STATION,
    _apply_demo_scenario,
    _build_event_log,
    _format_currency_uah,
    _format_time,
)
from src.routing import RouteResult


class TestAppHelpers(unittest.TestCase):
    def _graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        g.add_node("A", capacity=100.0, current_load=20.0, available_locomotives=5, lat=0.0, lon=0.0)
        g.add_node(ODESSA_SORT_STATION, capacity=100.0, current_load=20.0, available_locomotives=5, lat=0.0, lon=1.0)
        g.add_node("C", capacity=100.0, current_load=20.0, available_locomotives=5, lat=1.0, lon=1.0)
        g.add_edge("A", ODESSA_SORT_STATION, max_capacity=10.0, current_flow=1.0, base_time=1.0, waypoints=[])
        g.add_edge(ODESSA_SORT_STATION, "C", max_capacity=10.0, current_flow=1.0, base_time=1.0, waypoints=[])
        g.add_edge("A", "C", max_capacity=10.0, current_flow=1.0, base_time=1.0, waypoints=[])
        return g

    def test_formatters(self) -> None:
        self.assertEqual(_format_time(2.5), "2 hrs 30 mins")
        self.assertEqual(_format_currency_uah(12345), "12,345 ₴")
        self.assertEqual(_format_currency_uah(12345, signed=True), "+12,345 ₴")

    def test_apply_demo_scenario_odessa_bottleneck(self) -> None:
        graph = self._graph()

        _apply_demo_scenario(graph, "Odesa Bottleneck")

        self.assertEqual(graph.nodes[ODESSA_SORT_STATION]["available_locomotives"], 0)
        self.assertEqual(graph.nodes[ODESSA_SORT_STATION]["current_load"], 100.0)
        self.assertEqual(graph["A"][ODESSA_SORT_STATION]["current_flow"], 10.0)
        self.assertEqual(graph[ODESSA_SORT_STATION]["C"]["current_flow"], 10.0)

    def test_event_log_mentions_penalty_and_savings(self) -> None:
        graph = self._graph()
        _apply_demo_scenario(graph, "Odesa Bottleneck")
        smart = RouteResult(
            path=["A", "C"],
            total_cost=1000.0,
            segment_costs=[1000.0],
            total_time_hours=1.0,
            segment_times_hours=[1.0],
            total_distance_km=10.0,
            segment_distances_km=[10.0],
        )
        baseline = RouteResult(
            path=["A", ODESSA_SORT_STATION, "C"],
            total_cost=5000.0,
            segment_costs=[2500.0, 2500.0],
            total_time_hours=2.0,
            segment_times_hours=[1.0, 1.0],
            total_distance_km=20.0,
            segment_distances_km=[10.0, 10.0],
        )

        log_items = _build_event_log(
            graph=graph,
            scenario="Odesa Bottleneck",
            smart_route=smart,
            baseline_route=baseline,
            baseline_cost=5000.0,
            money_saved=4000.0,
        )

        self.assertTrue(any("0 locomotives" in item for item in log_items))
        self.assertTrue(any("AI rerouted" in item for item in log_items))
        self.assertTrue(any("+4,000 ₴" in item for item in log_items))


if __name__ == "__main__":
    unittest.main()
