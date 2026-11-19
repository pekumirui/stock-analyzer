from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CRONTAB = "deploy/crontab"
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_ASSIGN = re.compile(r"^([A-Z_]+)=(.*)$")
_COMMAND = re.compile(r"deploy/cron-run\.sh\s+(jobs/\S+\.py)([^;}]*)")
_DOW = "日月火水木金土"


@dataclass(frozen=True)
class Run:
    job: str
    args: str
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    dows: tuple[int, ...]
    expr: str
    line: int
    step: int
    chain: int

    @property
    def fires(self) -> list[tuple[int, int]]:
        return [(h, m) for h in self.hours for m in self.minutes]


def _expand(field: str, lo: int, hi: int) -> tuple[int, ...]:
    out: set[int] = set()
    for part in field.split(","):
        span, _, step = part.partition("/")
        if span == "*":
            a, b = lo, hi
        elif "-" in span:
            a, b = (int(x) for x in span.split("-"))
        else:
            a = b = int(span)
        if not lo <= a <= b <= hi:
            raise ValueError(f"crontab: {field} は {lo}〜{hi} に収まらない")
        out.update(range(a, b + 1, int(step or 1)))
    return tuple(sorted(out))


def parse(text: str) -> tuple[str | None, list[Run]]:
    tz = None
    runs: list[Run] = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assign = _ASSIGN.match(line)
        if assign:
            if assign.group(1) == "CRON_TZ":
                tz = assign.group(2)
            continue
        fields = line.split(None, 5)
        if len(fields) < 6:
            raise ValueError(f"crontab {no} 行目: 時刻 5 つとコマンドに分けられない")
        minutes, hours, doms, months, dows = (_expand(f, *b) for f, b in zip(fields, _BOUNDS))
        if len(doms) != 31 or len(months) != 12:
            raise ValueError(f"crontab {no} 行目: 日・月の指定は時刻表に出せない")
        found = _COMMAND.findall(fields[5])
        if not found:
            raise ValueError(f"crontab {no} 行目: deploy/cron-run.sh jobs/….py の形のコマンドが無い")
        for step, (job, args) in enumerate(found):
            runs.append(Run(
                job, args.strip(), minutes, hours, tuple(sorted({d % 7 for d in dows})),
                " ".join(fields[:5]), no, step, len(found),
            ))
    return tz, runs


def load(root: Path) -> tuple[str | None, list[Run]]:
    return parse((root / CRONTAB).read_text(encoding="utf-8"))


def _spans(values: tuple[int, ...]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for v in values:
        if out and out[-1][1] == v - 1:
            out[-1] = (out[-1][0], v)
        else:
            out.append((v, v))
    return out


def dow_text(dows: tuple[int, ...]) -> str:
    if len(dows) == 7:
        return "毎日"
    if dows == (1, 2, 3, 4, 5):
        return "平日"
    return "・".join(_DOW[a] if a == b else f"{_DOW[a]}〜{_DOW[b]}" for a, b in _spans(dows)) + "曜"


def time_text(run: Run) -> str:
    if len(run.hours) <= 2:
        return "・".join(f"{h:02d}:{m:02d}" for h, m in run.fires)
    hours = "・".join(str(a) if a == b else f"{a}〜{b}" for a, b in _spans(run.hours))
    return f"{hours} 時の毎時 " + "・".join(f"{m:02d}" for m in run.minutes) + " 分"


def schedule_text(run: Run) -> str:
    return f"{dow_text(run.dows)} {time_text(run)}"
