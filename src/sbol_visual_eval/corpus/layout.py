"""Filesystem layout of the corpus: every path is derived from one repository root."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Layout:
    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def annotations(self) -> Path:
        return self.raw / "annotations"

    @property
    def rubric(self) -> Path:
        return self.raw / "rubric"

    @property
    def metadata(self) -> Path:
        return self.raw / "metadata"

    @property
    def website(self) -> Path:
        return self.raw / "website"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def processed(self) -> Path:
        return self.data / "processed"

    @property
    def reports(self) -> Path:
        return self.data / "reports"

    @property
    def papers(self) -> Path:
        return self.data / "papers"

    def ensure_directories(self) -> None:
        for path in (
            self.annotations,
            self.rubric,
            self.metadata,
            self.website,
            self.cache,
            self.processed,
            self.reports,
            self.papers,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def paper_directory(self, record: dict[str, Any]) -> Path:
        identifier = record.get("doi") or record["record_id"]
        slug = re.sub(r"[^A-Za-z0-9._-]+", "__", identifier)
        return self.papers / str(record["year"]) / slug

    def display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)
