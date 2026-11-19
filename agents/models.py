"""Plain data models shared by the connectors and the agents.

They are source-agnostic: the same ``Change`` comes out of the local synthetic CSVs and (from M4) out of the
ServiceNow Table API, so every agent works the same way offline and against a live PDI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

TS_FMT = "%Y-%m-%d %H:%M:%S"


def parse_ts(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], TS_FMT)
    except ValueError:
        return None


# ServiceNow change_request.state
CHANGE_STATES = {"-5": "New", "-4": "Assess", "-3": "Authorize", "-2": "Scheduled", "-1": "Implement", "0": "Review",
                 "3": "Closed", "4": "Canceled"}
OPEN_CHANGE_STATES = {"-5", "-4", "-3", "-2", "-1"}     # still to happen: they can conflict with each other
REQUESTER_RISK = {"1": "very high", "2": "high", "3": "moderate", "4": "low"}


@dataclass
class Change:
    key: str                        # stable id: local "_key" (chg:0301) or ServiceNow number
    short_description: str
    description: str = ""
    number: Optional[str] = None
    sys_id: Optional[str] = None
    justification: str = ""
    implementation_plan: str = ""
    backout_plan: str = ""
    test_plan: str = ""
    type: str = "normal"            # standard | normal | emergency
    category: Optional[str] = None
    ci: Optional[str] = None
    business_service: Optional[str] = None
    assignment_group: Optional[str] = None
    requested_by: Optional[str] = None
    start: Optional[str] = None     # planned window (UTC)
    end: Optional[str] = None
    work_start: Optional[str] = None
    work_end: Optional[str] = None
    risk: Optional[str] = None      # the requester's own risk (2 high, 3 moderate, 4 low)
    impact: Optional[str] = None
    state: Optional[str] = None
    opened_at: Optional[str] = None
    work_notes: str = ""
    # Only meaningful on CLOSED changes (history). Redacted on open ones.
    close_code: Optional[str] = None
    close_notes: str = ""

    @property
    def text(self) -> str:
        return f"{self.short_description}\n{self.description}\n{self.implementation_plan}\n{self.backout_plan}"

    @property
    def start_dt(self) -> Optional[datetime]:
        return parse_ts(self.start)

    @property
    def end_dt(self) -> Optional[datetime]:
        return parse_ts(self.end)


@dataclass
class Incident:
    key: str
    short_description: str
    description: str = ""
    number: Optional[str] = None
    ci: Optional[str] = None
    business_service: Optional[str] = None
    category: Optional[str] = None
    impact: int = 3
    urgency: int = 3
    state: Optional[str] = None
    opened_at: Optional[str] = None
    resolved_at: Optional[str] = None
    assignment_group: Optional[str] = None
    caused_by: Optional[str] = None     # change key, when someone recorded the link
    work_notes: str = ""
    close_notes: str = ""

    @property
    def is_open(self) -> bool:
        return self.state in ("1", "2", "3")


@dataclass
class KBArticle:
    key: str
    title: str
    text: str
    symptoms: str = ""
    cause: str = ""
    resolver_group: Optional[str] = None
    runbook: Optional[str] = None
    safe_to_automate: bool = False
    meta: str = ""


@dataclass
class Problem:
    key: str
    short_description: str
    ci: Optional[str] = None
    known_error: bool = False
    cause_notes: str = ""
    workaround: str = ""
    state: Optional[str] = None
    opened_at: Optional[str] = None
    number: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.state not in ("106", "107")     # Resolved, Closed


@dataclass
class ConfigItem:
    name: str
    description: str = ""
    support_group: Optional[str] = None
    ci_class: str = ""
    service: Optional[str] = None       # business service it belongs to ("SKR Order Processing")
    tier: int = 3                       # 1 = most critical


@dataclass
class ScheduleSpan:
    schedule: str                       # schedule name
    kind: str                           # "blackout" (freeze) | "maintenance"
    applies_to: str                     # "all" | "tier 1" | "tier 2;tier 3" | "SKR Payments Integration;..."
    start: datetime                     # first occurrence (UTC)
    end: datetime
    repeat: str = ""                    # "" | "daily" | "weekly"
    days_of_week: str = ""              # weekly: ISO weekdays of the UTC start, e.g. "136" = Mon, Wed, Sat
    description: str = ""

    def applies(self, service: Optional[str], tier: int) -> bool:
        parts = [p.strip().lower() for p in self.applies_to.split(";") if p.strip()]
        return "all" in parts or f"tier {tier}" in parts or bool(service and service.lower() in parts)


@dataclass
class Assessment:
    """Everything the agents concluded about one change request."""
    change_key: str
    change_number: Optional[str]
    short_description: str
    quality: dict = field(default_factory=dict)
    impact: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)
    schedule: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    recommendation: str = ""
    conditions: list = field(default_factory=list)
    summary: str = ""
