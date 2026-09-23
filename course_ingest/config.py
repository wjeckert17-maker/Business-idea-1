from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class SchoolConfig:
    slug: str
    name: str
    timezone: str
    adapter: str
    base_url: str
    contact: str
    page_size: int = 500
    catalog_year_start_month: int = 8
    level_style: str = "auto"
    modality_maps: Dict[str, Dict[str, str]] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def user_agent(self) -> str:
        return f"course-planner-ingest/0.1 (+mailto:{self.contact}; polite crawler, 1 req/s)"

    @classmethod
    def load(cls, path: "str | Path") -> "SchoolConfig":
        data = tomllib.loads(Path(path).read_text())
        inst, src = data["institution"], data["source"]
        return cls(
            slug=inst["slug"], name=inst["name"], timezone=inst["timezone"],
            adapter=src["adapter"], base_url=src["base_url"], contact=src["contact"],
            page_size=int(src.get("page_size", 500)),
            catalog_year_start_month=int(data.get("catalog_year", {}).get("start_month", 8)),
            level_style=data.get("courses", {}).get("level_style", "auto"),
            modality_maps=data.get("modality", {}),
            raw=data,
        )
