from __future__ import annotations

import re
from abc import ABC, abstractmethod

from pipeline.compliance import CompliantHttpClient
from pipeline.models import StagedScholarship

MONEY_RE = re.compile(r"\$([0-9,]+)")


def parse_award_range(text: str) -> tuple[int | None, int | None]:
    """Extract min and max USD amounts from a text string like '$1,000 - $5,000'."""
    matches = [int(m.replace(",", "")) for m in MONEY_RE.findall(text or "")]
    if not matches:
        return None, None
    return min(matches), max(matches)


class BaseAdapter(ABC):
    name: str = "base"

    def __init__(self, client: CompliantHttpClient, config: dict) -> None:
        self.client = client
        self.config = config

    @abstractmethod
    def fetch(self) -> list[StagedScholarship]:
        raise NotImplementedError
