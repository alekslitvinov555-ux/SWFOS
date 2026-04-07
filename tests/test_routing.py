from __future__ import annotations

import unittest

import networkx as nx

from src.routing import find_optimal_route


class TestRouting(unittest.TestCase):
    def _build_base_graph(self) -> nx.DiGraph:
        """
        Build a small deterministic graph with two candidate routes:
        1) A -> B -> D (faster in base time)
        2) A -> C -> D (slower in base time)
        """
        g = nx.DiGraph()

        g.add_node("A", capacity=100, current_load=10, lat=0.0, lon=0.0)
        g.add_node("B", capacity=100, current_load=20, lat=0.0, lon=1.0)
        g.add_node("C", capacity=100, current_load=20, lat=1.0, lon=0.0)
        g.add_node("D", capacity=100, current_load=20, lat=1.0, lon=1.0)

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

    def test_overloaded_station_above_90_percent_is_avoided(self) -> None:
        graph = self._build_base_graph()

        # Overload B above 90% to trigger strong station penalty.
        graph.nodes["B"]["current_load"] = 95

        result = find_optimal_route(graph, source="A", target="D")

        self.assertEqual(result.path, ["A", "C", "D"])
        self.assertGreater(result.total_cost, 0)
        self.assertEqual(len(result.segment_costs), 2)


if __name__ == "__main__":
    unittest.main()
