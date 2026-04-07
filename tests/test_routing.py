from __future__ import annotations

import unittest
from pathlib import Path

import networkx as nx

from app import DERAILMENT_SCENARIO, _apply_demo_scenario
from src.graph_builder import build_graph
from src.routing import calculate_route_cost, find_optimal_route, find_shortest_distance_route


class TestRouting(unittest.TestCase):
    def _build_base_graph(self) -> nx.DiGraph:
        """
        Build a small deterministic graph with two candidate routes:
        1) A -> B -> D (faster in base time)
        2) A -> C -> D (slower in base time)
        """
        g = nx.DiGraph()

        g.add_node("A", capacity=100, current_load=10, available_locomotives=5, lat=0.0, lon=0.0)
        g.add_node("B", capacity=100, current_load=20, available_locomotives=5, lat=0.0, lon=0.2)
        g.add_node("C", capacity=100, current_load=20, available_locomotives=5, lat=0.8, lon=0.0)
        g.add_node("D", capacity=100, current_load=20, available_locomotives=5, lat=1.0, lon=1.0)

        g.add_edge("A", "B", base_time=1.0, max_capacity=100, current_flow=10)
        g.add_edge("B", "D", base_time=1.0, max_capacity=100, current_flow=10)

        g.add_edge("A", "C", base_time=2.0, max_capacity=100, current_flow=10)
        g.add_edge("C", "D", base_time=2.0, max_capacity=100, current_flow=10)

        return g

    def test_happy_path_prefers_fastest_route_when_not_overloaded(self) -> None:
        graph = self._build_base_graph()

        result = find_optimal_route(graph, source="A", target="D")

        self.assertEqual(result.path, ["A", "B", "D"])
        self.assertGreater(result.total_cost, 0)
        self.assertEqual(len(result.segment_costs), 2)
        self.assertGreater(result.total_time_hours, 0)
        self.assertGreater(result.total_distance_km, 0)

    def test_overloaded_station_above_90_percent_is_avoided(self) -> None:
        graph = self._build_base_graph()

        # Overload B above 90% to trigger strong station penalty.
        graph.nodes["B"]["current_load"] = 95
        graph["A"]["B"]["current_flow"] = graph["A"]["B"]["max_capacity"]
        graph["B"]["D"]["current_flow"] = graph["B"]["D"]["max_capacity"]

        result = find_optimal_route(graph, source="A", target="D")

        self.assertEqual(result.path, ["A", "C", "D"])
        self.assertGreater(result.total_cost, 0)
        self.assertEqual(len(result.segment_costs), 2)

    def test_zero_locomotives_adds_soft_penalty_and_discourages_path(self) -> None:
        graph = self._build_base_graph()
        graph.nodes["B"]["available_locomotives"] = 0

        result = find_optimal_route(graph, source="A", target="D")
        self.assertEqual(result.path, ["A", "C", "D"])

    def test_ac_dc_switch_adds_penalty_and_can_shift_route(self) -> None:
        graph = self._build_base_graph()
        graph.nodes["B"]["is_ac_dc_switch"] = True
        graph["A"]["C"]["base_time"] = 1.05
        graph["C"]["D"]["base_time"] = 1.05

        result = find_optimal_route(graph, source="A", target="D")
        self.assertEqual(result.path, ["A", "C", "D"])

    def test_shortest_distance_route_baseline_is_available(self) -> None:
        graph = self._build_base_graph()

        baseline = find_shortest_distance_route(graph, source="A", target="D")

        self.assertEqual(baseline.path, ["A", "B", "D"])
        self.assertGreater(baseline.total_cost, 0)

    def test_calculate_route_cost_uses_given_time(self) -> None:
        graph = self._build_base_graph()
        route = ["A", "B", "D"]

        with_given_time = calculate_route_cost(graph, route=route, time_hours=5.0)
        with_derived_time = calculate_route_cost(graph, route=route, time_hours=None)

        self.assertGreater(with_given_time, 0)
        self.assertGreater(with_derived_time, 0)
        self.assertNotEqual(with_given_time, with_derived_time)

    def test_odessa_bottleneck_prefers_bypass_route(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        graph = build_graph(project_root / "data" / "stations.json", project_root / "data" / "edges.json")

        _apply_demo_scenario(graph, "Odesa Bottleneck")
        result = find_optimal_route(graph, source="Kolosivka", target="Odesa-Port")

        self.assertIn("Chornomorsk Port", result.path)
        self.assertNotIn("Odesa-Sortuvalna", result.path)

    def test_derailment_uses_bypass_via_pomichna_and_borshchivka(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        graph = build_graph(project_root / "data" / "stations.json", project_root / "data" / "edges.json")

        _apply_demo_scenario(graph, DERAILMENT_SCENARIO)
        result = find_optimal_route(graph, source="Odesa-Skhidna", target="Kulyndorove")

        self.assertIn("Pomichna", result.path)
        self.assertIn("Borshchivka", result.path)
        self.assertNotEqual(result.path, ["Odesa-Skhidna", "Kulyndorove"])


if __name__ == "__main__":
    unittest.main()
