from __future__ import annotations

import unittest
from unittest.mock import patch

from dabo_kb.review import review_document


class ReviewWorkflowTests(unittest.TestCase):
    def test_review_document_connects_fetch_video_and_targeted_ocr(self) -> None:
        with (
            patch(
                "dabo_kb.review.fetch_public_candidates",
                return_value={"resolved": 1, "failures": []},
            ) as fetch,
            patch(
                "dabo_kb.review.download_video_evidence",
                return_value={"state": "video_evidence_ready"},
            ) as download,
            patch(
                "dabo_kb.review.ocr_video",
                return_value={"temporary_video_removed": True},
            ) as ocr,
        ):
            result = review_document("123")
        fetch.assert_called_once_with(source_id="123", workers=1)
        download.assert_called_once_with("123")
        ocr.assert_called_once_with("123", interval=10, targeted=True)
        self.assertEqual(result["id"], "123")


if __name__ == "__main__":
    unittest.main()
