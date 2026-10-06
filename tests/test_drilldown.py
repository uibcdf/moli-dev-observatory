"""Tests for Development Observatory drill-down views."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from moli_dev_observatory.core import metrics
from moli_dev_observatory.drilldown import (
    generate_drilldowns,
    repository_slug,
    slugify,
    subset_dataset,
)


DATASET = {
    "schema_version": 1,
    "generated_at": "2026-10-06T06:00:00Z",
    "provenance": {"issue_lifecycle_model": "snapshot-v1"},
    "scope": [
        {
            "repository": "uibcdf/moli",
            "layer": "MOLI",
            "role": "platform",
            "source": "moli.toml",
        },
        {
            "repository": "uibcdf/topomt",
            "layer": "Scientific components",
            "role": "scientific-component",
            "source": "suite.toml",
        },
    ],
    "excluded_scope": [
        {
            "repository": "uibcdf/private",
            "layer": "Scientific components",
            "role": "scientific-component",
            "source": "suite.toml",
            "status": 404,
            "reason": "inaccessible",
        }
    ],
    "issues": [
        {
            "repository": "uibcdf/moli",
            "state": "closed",
            "created_at": "2026-10-01T10:00:00Z",
            "closed_at": "2026-10-02T10:00:00Z",
        },
        {
            "repository": "uibcdf/topomt",
            "state": "open",
            "created_at": "2026-10-03T10:00:00Z",
            "closed_at": None,
        },
        {
            "repository": "uibcdf/topomt",
            "state": "closed",
            "created_at": "2026-10-04T10:00:00Z",
            "closed_at": "2026-10-05T10:00:00Z",
        },
    ],
}


class ObservatoryDrilldownTests(unittest.TestCase):
    def test_stable_slugs(self):
        self.assertEqual(slugify("Scientific components"), "scientific-components")
        self.assertEqual(repository_slug("uibcdf/topomt"), "uibcdf--topomt")

    def test_subset_filters_scope_issues_and_exclusions(self):
        layer = subset_dataset(DATASET, layer="Scientific components")
        self.assertEqual(
            [item["repository"] for item in layer["scope"]],
            ["uibcdf/topomt"],
        )
        self.assertEqual(len(layer["issues"]), 2)
        self.assertEqual(layer["excluded_scope"][0]["repository"], "uibcdf/private")

        repository = subset_dataset(DATASET, repository="uibcdf/moli")
        self.assertEqual(len(repository["scope"]), 1)
        self.assertEqual(len(repository["issues"]), 1)
        self.assertEqual(repository["excluded_scope"], [])

    def test_generation_builds_linked_static_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "issues.json").write_text(
                json.dumps(DATASET),
                encoding="utf-8",
            )
            write_root_metrics = metrics(DATASET, 7, "UTC")
            (output / "metrics.json").write_text(
                json.dumps(write_root_metrics),
                encoding="utf-8",
            )

            generate_drilldowns(
                output,
                DATASET,
                days=7,
                timezone_name="UTC",
                metrics_fn=metrics,
            )

            overview = (output / "index.html").read_text(encoding="utf-8")
            layer_path = output / "layers/scientific-components"
            repository_path = output / "repositories/uibcdf--topomt"

            self.assertTrue((layer_path / "index.html").is_file())
            self.assertTrue((layer_path / "metrics.json").is_file())
            self.assertTrue((repository_path / "index.html").is_file())
            self.assertTrue((repository_path / "metrics.json").is_file())

            self.assertIn("layers/", overview)
            self.assertIn("repositories/", overview)
            self.assertIn("Click a repository to drill down", overview)

            layer_html = (layer_path / "index.html").read_text(encoding="utf-8")
            self.assertIn("../../repositories/", layer_html)
            self.assertIn('href="../../"', layer_html)

            repository_html = (repository_path / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("../../layers/", repository_html)
            self.assertIn("uibcdf/topomt", repository_html)

            layer_metrics = json.loads(
                (layer_path / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(layer_metrics["summary"]["repositories"], 1)
            self.assertEqual(layer_metrics["summary"]["excluded_repositories"], 1)

            repository_metrics = json.loads(
                (repository_path / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(repository_metrics["summary"]["repositories"], 1)
            self.assertEqual(repository_metrics["summary"]["current_open"], 1)
            self.assertEqual(repository_metrics["repositories"][0]["total"], 2)


if __name__ == "__main__":
    unittest.main()
