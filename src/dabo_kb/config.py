from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db(self) -> Path:
        return self.data / "index" / "knowledge.db"

    @property
    def qdrant(self) -> Path:
        return self.data / "index" / "qdrant"

    @property
    def media(self) -> Path:
        return self.data / "media"

    @property
    def sources(self) -> Path:
        return self.data / "sources"

    @property
    def tmp(self) -> Path:
        return self.data / "tmp"

    @property
    def transcripts(self) -> Path:
        return self.data / "transcripts"

    @property
    def graph(self) -> Path:
        return self.data / "graph"

    def ensure(self) -> None:
        for path in (
            self.db.parent,
            self.qdrant,
            self.media,
            self.sources,
            self.transcripts,
            self.graph,
            self.tmp,
        ):
            path.mkdir(parents=True, exist_ok=True)


def find_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (Path.cwd(), *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


SETTINGS = Settings(find_root())

WHISPER_MODEL = "small"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
