"""Schedule agent: maintenance windows, business hours, freezes, conflicts and lead time."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional

from .connectors.base import ITSMClient
from .models import OPEN_CHANGE_STATES, Change, ConfigItem, ScheduleSpan, parse_ts
from .policy import BUSINESS_HOURS_IST, MIN_LEAD_HOURS

IST = timedelta(hours=5, minutes=30)


def in_business_hours(s: datetime, e: datetime) -> bool:
    lo, hi = BUSINESS_HOURS_IST
    t = s
    while t < e:
        ti = t + IST
        if ti.weekday() < 5 and lo <= ti.hour < hi:
            return True
        t += timedelta(minutes=15)
    return False


def covers(span: ScheduleSpan, s: datetime, e: datetime) -> bool:
    """Is [s, e] entirely inside one occurrence of the span?"""
    dur = span.end - span.start
    for day in (s.date() - timedelta(days=1), s.date()):
        if day < span.start.date():
            continue
        if span.repeat == "weekly" and str(day.isoweekday()) not in span.days_of_week:
            continue
        if not span.repeat and day != span.start.date():
            continue
        occ = datetime.combine(day, span.start.time())
        if occ <= s and e <= occ + dur:
            return True
    return False


class ScheduleAgent:
    name = "schedule"

    def __init__(self, itsm: ITSMClient):
        self.itsm = itsm
        spans = itsm.schedules()
        self.maintenance = [s for s in spans if s.kind == "maintenance"]
        self.freezes = [s for s in spans if s.kind == "blackout"]

    def window(self, ci: Optional[ConfigItem], s: datetime, e: datetime) -> tuple[str, Optional[str]]:
        service, tier = (ci.service, ci.tier) if ci else (None, 3)
        for span in self.maintenance:
            if span.applies(service, tier) and covers(span, s, e):
                return "maint", span.schedule
        return ("business" if in_business_hours(s, e) else "off_hours"), None

    def freeze_hits(self, ci: Optional[ConfigItem], s: datetime, e: datetime) -> list[str]:
        service, tier = (ci.service, ci.tier) if ci else (None, 3)
        return sorted({f.schedule for f in self.freezes
                       if f.applies(service, tier) and f.start < e and s < f.end})

    def conflicts(self, ch: Change, pool: Iterable[Change]) -> list[dict]:
        s, e = ch.start_dt, ch.end_dt
        if not (s and e and ch.ci):
            return []
        related = self.itsm.related(ch.ci)
        out = []
        for o in pool:
            os_, oe = o.start_dt, o.end_dt
            if o.key == ch.key or not (os_ and oe) or not (os_ < e and s < oe):
                continue
            if o.ci == ch.ci:
                rel = "same_ci"
            elif o.ci in related:
                rel = "related_ci"
            else:
                continue
            out.append({"key": o.key, "relation": rel, "ci": o.ci, "short_description": o.short_description,
                        "start": o.start, "end": o.end, "state": o.state})
        return out

    def run(self, ch: Change, pool: Optional[Iterable[Change]] = None) -> dict:
        s, e = ch.start_dt, ch.end_dt
        if not (s and e):
            return {"window": "unknown", "freeze": [], "conflicts": [], "short_lead": False}
        ci = self.itsm.config_item(ch.ci) if ch.ci else None
        window, window_name = self.window(ci, s, e)
        if pool is None:
            pool = [c for c in self.itsm.changes() if c.state in OPEN_CHANGE_STATES]
        opened = parse_ts(ch.opened_at)
        lead = round((s - opened).total_seconds() / 3600, 1) if opened else None
        return {"start": ch.start, "end": ch.end, "window": window, "maintenance_window": window_name,
                "freeze": self.freeze_hits(ci, s, e), "conflicts": self.conflicts(ch, pool), "lead_time_hours": lead,
                "short_lead": ch.type == "normal" and lead is not None and lead < MIN_LEAD_HOURS}
