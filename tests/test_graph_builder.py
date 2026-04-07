from __future__ import annotations

import unittest
from pathlib import Path

from src.graph_builder import build_graph


class TestGraphBuilder(unittest.TestCase):
    def test_build_graph_from_mock_data(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        stations = project_root / "data" / "stations.json"
        edges = project_root / "data" / "edges.json"

        graph = build_graph(stations, edges)

        self.assertEqual(len(graph.nodes), 16)
        self.assertIn("Kryvyi Rih", graph.nodes)
        self.assertIn("Odesa-Sortuvalna", graph.nodes)
        self.assertIn("Chornomorsk-Bypass", graph.nodes)
        self.assertIn("Izmail", graph.nodes)
        self.assertEqual(graph.nodes["Kryvyi Rih"]["available_locomotives"], 5)
        self.assertEqual(graph.nodes["Odesa-Sortuvalna"]["available_locomotives"], 0)
        self.assertEqual(len(graph.edges), 32)

        forward_waypoints = graph["Odesa-Sortuvalna"]["Bilhorod-Dnistrovskyi"]["waypoints"]
        reverse_waypoints = graph["Bilhorod-Dnistrovskyi"]["Odesa-Sortuvalna"]["waypoints"]

        self.assertGreaterEqual(len(forward_waypoints), 15)
        self.assertEqual(forward_waypoints[0], [46.53, 30.77])
        self.assertEqual(forward_waypoints[-1], [46.19, 30.35])
        self.assertEqual(reverse_waypoints, list(reversed(forward_waypoints)))


if __name__ == "__main__":
    unittest.main()
