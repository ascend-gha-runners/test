import json
import unittest
from pathlib import Path

from select_pypi_runner_matrix import select_runners

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / ".github/config/runners.json").read_text(encoding="utf-8"))


class MatrixSelectionTests(unittest.TestCase):
    def test_all_configured_clusters_covered_once(self):
        selected = select_runners(CONFIG)
        all_clusters = {r["cluster"] for r in CONFIG["runners"]}
        self.assertEqual(len(all_clusters), 13)
        self.assertEqual({r["cluster"] for r in selected}, all_clusters)
        self.assertEqual(len(selected), len(all_clusters))
        self.assertEqual(len({r["name"] for r in selected}), len(selected))

    def test_cpu_preferred_and_npu_fallback(self):
        selected = select_runners(CONFIG)
        configured_cpu = {
            r["cluster"] for r in CONFIG["runners"] if r["type"] == "cpu"
        }
        self.assertEqual(len(configured_cpu), 8)
        self.assertEqual(sum(r["type"] == "npu" for r in selected), 5)
        for row in selected:
            self.assertEqual(row["type"], "cpu" if row["cluster"] in configured_cpu else "npu")
            self.assertEqual(row["labels"][-1], row["cluster"])

    def test_single_cluster_and_unknown(self):
        selected = select_runners(CONFIG, "mind-third-ci")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["type"], "cpu")
        with self.assertRaisesRegex(ValueError, "Unknown cluster"):
            select_runners(CONFIG, "not-a-cluster")

    def test_bad_runner_declaration_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid runner"):
            select_runners({"runners": [{"cluster": "c", "type": "cpu", "labels": []}]})


if __name__ == "__main__":
    unittest.main()
