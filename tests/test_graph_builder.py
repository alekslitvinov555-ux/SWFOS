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

        self.assertEqual(len(graph.nodes), 14)
        self.assertIn("Kryvyi Rih", graph.nodes)
        self.assertIn("Tymkove", graph.nodes)
        self.assertIn("Kropyvnytska", graph.nodes)
        self.assertIn("Dnipro", graph.nodes)
        self.assertIn("Pyatykhatky", graph.nodes)
        self.assertIn("Odesa-Skhidna", graph.nodes)
        self.assertIn("Kulyndorove", graph.nodes)
        self.assertIn("Borshchivka", graph.nodes)
        self.assertIn("Odesa-Sortuvalna", graph.nodes)
        self.assertEqual(graph.nodes["Kryvyi Rih"]["available_locomotives"], 5)
        self.assertTrue(graph.nodes["Tymkove"]["is_ac_dc_switch"])
        self.assertTrue(graph.nodes["Pyatykhatky"]["is_ac_dc_switch"])
        self.assertEqual(graph.nodes["Odesa-Sortuvalna"]["available_locomotives"], 0)
        self.assertEqual(len(graph.edges), 28)

        self.assertEqual(graph["Kryvyi Rih"]["Tymkove"]["track_type"], "double")
        self.assertEqual(graph["Tymkove"]["Kropyvnytska"]["track_type"], "single")
        self.assertEqual(graph["Odesa-Skhidna"]["Kulyndorove"]["track_type"], "single")
        self.assertEqual(graph["Kulyndorove"]["Odesa-Skhidna"]["track_type"], "single")


if __name__ == "__main__":
    unittest.main()
