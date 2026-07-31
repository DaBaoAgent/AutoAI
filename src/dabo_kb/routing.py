from __future__ import annotations

import re


PROJECT_RISK = re.compile(
    r"(?i)(github|gitlab|开源|热榜|排行榜|榜单|项目盘点|一周热点|"
    r"repo(?:sitory)?|仓库|开源项目)"
)
ENGLISH_NAME = re.compile(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b")
OWNER_REPO = re.compile(
    r"\b[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\b"
)


def route_title(title: str) -> dict[str, object]:
    """Choose the fast or proper-noun ASR lane from durable title metadata."""
    strict_hits = sorted(
        {match.group(0) for match in PROJECT_RISK.finditer(title)},
        key=str.casefold,
    )
    owner_repo_hits = OWNER_REPO.findall(title)
    english_hits = ENGLISH_NAME.findall(title)
    lane = "small" if strict_hits or owner_repo_hits else "base"
    return {
        "lane": lane,
        "strict_hits": strict_hits,
        "owner_repo_hits": owner_repo_hits,
        "english_token_count": len(english_hits),
        "needs_name_review": lane == "small",
    }


def transcript_needs_visual_review(text: str, title: str = "") -> bool:
    """Flag likely spelling-sensitive transcripts for the existing OCR gate."""
    routed = route_title(title)
    if routed["needs_name_review"]:
        return True
    english = ENGLISH_NAME.findall(text)
    owner_repo = OWNER_REPO.findall(text)
    return len(english) >= 8 or bool(owner_repo)
