from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dabo_kb.ocr import (
    _filter_redundant_projects,
    _project_evidence,
    _repository_evidence,
    _transcript_target_times,
)


class TargetFrameTests(unittest.TestCase):
    def test_uses_rank_segment_and_following_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcripts = root / "transcripts"
            transcripts.mkdir()
            (transcripts / "123.json").write_text(
                json.dumps(
                    {
                        "segments": [
                            {"start": 10, "text": "第十八名"},
                            {"start": 11, "text": "Project Name"},
                            {"start": 30, "text": "普通说明"},
                            {"start": 40, "text": "最后一个项目"},
                            {"start": 42, "text": "Final Project"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch(
                "dabo_kb.ocr.SETTINGS",
                SimpleNamespace(transcripts=transcripts),
            ):
                self.assertEqual(
                    _transcript_target_times("123"),
                    [10.5, 11.5, 40.5, 42.5],
                )

    def test_repository_gate_rejects_page_paths_and_truncated_urls(self) -> None:
        records = [
            {
                "time": 12,
                "frame": "frame.jpg",
                "lines": [
                    {"text": "github.com/Owner/Real-Repo"},
                    {"text": "owner/Header-Repo Public"},
                    {"text": "docs/next"},
                    {"text": "github/workflows"},
                    {"text": "github.com/owner/sti..."},
                ],
            }
        ]
        evidence = _repository_evidence(records, [])
        self.assertEqual(
            sorted(item["name"] for item in evidence.values()),
            ["Owner/Real-Repo", "owner/Header-Repo"],
        )

    def test_ranked_project_uses_complete_nearby_label(self) -> None:
        records = [
            {
                "time": 207,
                "frame": "rank.jpg",
                "lines": [{"text": "第二名 Desktop Commander MC"}],
            },
            {
                "time": 208,
                "frame": "name.jpg",
                "lines": [
                    {"text": "Desktop Commander"},
                    {"text": "MCP"},
                ],
            },
        ]
        evidence = _project_evidence(records)
        self.assertIn("desktop commander mcp", evidence)

    def test_repository_basename_suppresses_noisy_project_alias(self) -> None:
        projects = {
            "officecli ppt": {"name": "OfficeCLI PPT"},
            "desktop commander mcp": {"name": "Desktop Commander MCP"},
        }
        repositories = {
            "iofficeai/officecli": {"name": "iOfficeAI/OfficeCLI"},
        }
        filtered = _filter_redundant_projects(projects, repositories)
        self.assertEqual(
            [item["name"] for item in filtered.values()],
            ["Desktop Commander MCP"],
        )


if __name__ == "__main__":
    unittest.main()
