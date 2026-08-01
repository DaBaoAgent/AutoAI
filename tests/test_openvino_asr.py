from __future__ import annotations

import unittest
from unittest.mock import patch

from dabo_kb.openvino_asr import _pending_rows


class _Rows:
    def execute(self, _sql: str, _params: tuple[object, ...]):
        return self

    def fetchall(self):
        return [
            {"source_id": "100000000000000001", "title": "missing", "status": "discovered"},
            {"source_id": "100000000000000002", "title": "ready one", "status": "discovered"},
            {"source_id": "100000000000000003", "title": "ready two", "status": "discovered"},
        ]


class _Connection:
    def __enter__(self):
        return _Rows()

    def __exit__(self, *_args):
        return False


class PendingRowsTests(unittest.TestCase):
    def test_limit_applies_after_audio_filter(self) -> None:
        with (
            patch("dabo_kb.openvino_asr.connect", return_value=_Connection()),
            patch(
                "dabo_kb.openvino_asr.Path.exists",
                new=lambda path: str(path).endswith(("002.m4a", "003.m4a")),
            ),
        ):
            rows = _pending_rows(source_id=None, limit=1, force=False)
        self.assertEqual(
            [row["source_id"] for row in rows],
            ["100000000000000002"],
        )


if __name__ == "__main__":
    unittest.main()
