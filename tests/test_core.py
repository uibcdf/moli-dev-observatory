"""Tests for the MOLI Development Observatory."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from moli_dev_observatory.core import (
    GitHubAPIError,
    collect,
    dashboard_html,
    discover_scope,
    metrics,
)

MOLI = {
    "components": {
        "sabueso": {"repository": "uibcdf/sabueso", "role": "knowledge"},
        "molsyssuite": {"repository": "uibcdf/molsyssuite", "role": "modeling-ecosystem"},
    },
    "support_infrastructure": {
        "resources": [
            {"repository": "uibcdf/pytest-receptor", "kind": "developer-receptor"},
            {"repository": "uibcdf/external-action", "kind": "github-action"},
        ]
    },
}
SUITE = {
    "members": [
        {"repository": "uibcdf/molsysmt", "role": "scientific-component"},
        {"repository": "uibcdf/pytest-receptor", "role": "developer-tool"},
    ]
}


class ObservatoryTests(unittest.TestCase):
    def test_scope_is_registry_driven_and_deduplicated(self):
        scope = discover_scope(MOLI, SUITE)
        indexed = {item["repository"]: item for item in scope}
        self.assertEqual(indexed["uibcdf/moli"]["layer"], "MOLI")
        self.assertEqual(indexed["uibcdf/molsyssuite"]["layer"], "MolSysSuite")
        self.assertEqual(indexed["uibcdf/molsysmt"]["layer"], "Scientific components")
        self.assertEqual(indexed["uibcdf/pytest-receptor"]["layer"], "Infrastructure")
        self.assertEqual(len(indexed), len(scope))

    def test_metrics_capture_flow_layers_and_age(self):
        dataset = {
            "generated_at": "2026-10-05T12:00:00Z",
            "scope": [
                {"repository": "uibcdf/moli", "layer": "MOLI"},
                {"repository": "uibcdf/molsysmt", "layer": "Scientific components"},
            ],
            "issues": [
                {"repository": "uibcdf/moli", "state": "closed", "created_at": "2026-10-01T10:00:00Z", "closed_at": "2026-10-03T10:00:00Z"},
                {"repository": "uibcdf/molsysmt", "state": "open", "created_at": "2026-10-02T10:00:00Z", "closed_at": None},
                {"repository": "uibcdf/molsysmt", "state": "closed", "created_at": "2026-10-02T11:00:00Z", "closed_at": "2026-10-04T10:00:00Z"},
            ],
            "provenance": {"issue_lifecycle_model": "snapshot-v1"},
        }
        result = metrics(dataset, days=5, timezone_name="UTC")
        self.assertEqual(result["summary"]["opened"], 3)
        self.assertEqual(result["summary"]["closed"], 2)
        self.assertEqual(result["summary"]["net_change"], 1)
        self.assertEqual(result["summary"]["current_open"], 1)
        self.assertEqual(result["daily"][-1]["cumulative_net_change"], 1)
        science = next(item for item in result["layers"] if item["layer"] == "Scientific components")
        self.assertEqual(science["opened"], 2)
        self.assertEqual(science["closed"], 1)
        self.assertEqual(sum(item["count"] for item in result["issue_age"]), 1)

    def test_metrics_keep_inactive_repositories_visible(self):
        dataset = {
            "generated_at": "2026-10-05T12:00:00Z",
            "scope": [
                {"repository": "uibcdf/active", "layer": "MOLI"},
                {"repository": "uibcdf/quiet", "layer": "Infrastructure"},
            ],
            "issues": [
                {
                    "repository": "uibcdf/active",
                    "state": "closed",
                    "created_at": "2026-10-05T10:00:00Z",
                    "closed_at": "2026-10-05T11:00:00Z",
                }
            ],
            "excluded_scope": [],
            "provenance": {"issue_lifecycle_model": "snapshot-v1"},
        }
        result = metrics(dataset, days=1, timezone_name="UTC")
        rows = {item["repository"]: item for item in result["repositories"]}
        self.assertIn("uibcdf/quiet", rows)
        self.assertEqual(rows["uibcdf/quiet"]["opened"], 0)
        self.assertEqual(rows["uibcdf/quiet"]["closed"], 0)
        self.assertEqual(rows["uibcdf/quiet"]["current_open"], 0)
        self.assertEqual(rows["uibcdf/quiet"]["current_closed"], 0)
        self.assertEqual(rows["uibcdf/quiet"]["total"], 0)

    def test_collection_skips_inaccessible_repositories_but_records_them(self):
        scope = [
            {"repository": "uibcdf/public", "layer": "MOLI", "role": "component", "source": "moli.toml"},
            {"repository": "uibcdf/private", "layer": "Infrastructure", "role": "member", "source": "suite.toml"},
        ]

        def fake_repository_issues(repository, token):
            if repository == "uibcdf/private":
                raise GitHubAPIError(404, "https://api.github.test/private", '{"message":"Not Found"}')
            return [
                {
                    "id": 1,
                    "number": 1,
                    "title": "Visible",
                    "state": "open",
                    "state_reason": None,
                    "created_at": "2026-10-05T10:00:00Z",
                    "updated_at": "2026-10-05T10:00:00Z",
                    "closed_at": None,
                    "labels": [],
                    "html_url": "https://github.com/uibcdf/public/issues/1",
                }
            ]

        with patch(
            "moli_dev_observatory.core.repository_issues",
            side_effect=fake_repository_issues,
        ):
            dataset = collect(scope, None)

        self.assertEqual(
            [item["repository"] for item in dataset["scope"]],
            ["uibcdf/public"],
        )
        self.assertEqual(dataset["excluded_scope"][0]["repository"], "uibcdf/private")
        self.assertEqual(dataset["excluded_scope"][0]["status"], 404)
        result = metrics(dataset, days=1, timezone_name="UTC")
        self.assertEqual(result["summary"]["repositories"], 1)
        self.assertEqual(result["summary"]["excluded_repositories"], 1)

    def test_collection_aborts_on_rate_limit_instead_of_excluding(self):
        scope = [
            {"repository": "uibcdf/public", "layer": "MOLI", "role": "component", "source": "moli.toml"}
        ]
        error = GitHubAPIError(
            403,
            "https://api.github.test/public",
            '{"message":"API rate limit exceeded"}',
            rate_limit_remaining="0",
            rate_limit_reset="1791234000",
        )
        with patch(
            "moli_dev_observatory.core.repository_issues",
            side_effect=error,
        ):
            with self.assertRaisesRegex(RuntimeError, "rate limit exhausted"):
                collect(scope, None)

    def test_collection_keeps_unexpected_api_errors_fatal(self):
        scope = [
            {"repository": "uibcdf/broken", "layer": "MOLI", "role": "component", "source": "moli.toml"}
        ]
        with patch(
            "moli_dev_observatory.core.repository_issues",
            side_effect=GitHubAPIError(500, "https://api.github.test/broken", "server error"),
        ):
            with self.assertRaises(GitHubAPIError):
                collect(scope, None)

    def test_html_consumes_json_and_documents_portability(self):
        html = dashboard_html()
        self.assertIn("fetch('metrics.json')", html)
        self.assertIn("issues.json", html)
        self.assertIn("Grafana", html)
        self.assertIn("MOLI Development Observatory", html)
        self.assertIn('data-sort="total"', html)
        self.assertIn('data-sort="current_open"', html)
        self.assertIn('data-sort="current_closed"', html)
        self.assertIn('data-sort="net_change"', html)
        self.assertIn(">Total<", html)
        self.assertIn(">Open<", html)
        self.assertIn(">Closed<", html)


if __name__ == "__main__":
    unittest.main()
