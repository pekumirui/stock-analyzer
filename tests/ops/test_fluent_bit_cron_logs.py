"""cron-run.sh のログが Fluent Bit の tail に入っているか。

deploy/ の crontab と、timer から起動する service の ExecStart を見る。
ログのパスは deploy/cron-run.sh と同じ規則（既定は /var/log/app/<stem>.log）。
Fluent Bit の Path は glibc glob（* は / を跨がない）、Exclude_Path は fnmatch(flags=0)。
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ジョブ名（cron-run.sh がログファイル名に使う stem）→ 対象外にする理由。
# 理由が空、または crontab / timer に無い名前はテストが失敗する。
EXCLUDED_CRON_JOBS: dict[str, str] = {}

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_JOB_RE = re.compile(r"jobs/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.py")
_TIMER_SCHEDULE = re.compile(
    r"(?im)^\s*on(?:calendar|bootsec|unitactivesec|activesec|startupsec)\s*="
)
_SHELL_BREAKS = set(";|&)}({<>\n")


@dataclass(frozen=True)
class CronJob:
    name: str
    log_path: str
    scripts: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class TailInput:
    paths: tuple[str, ...]
    excludes: tuple[str, ...]


@dataclass(frozen=True)
class CoverageGap:
    name: str
    log_path: str
    sources: tuple[str, ...]


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def cron_run_log_dir(path: Path) -> str:
    """cron-run.sh の既定ログディレクトリ。命名規則が変わったら落とす。"""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^LOG_DIR=\$\{CRON_LOG_DIR:-([^}\n]+)\}", text, re.M)
    if match is None:
        _fail(f"{path} の LOG_DIR 既定値が見つかりません")
    required = (
        'log="$LOG_DIR/$job.log"',
        "job=${base%.py}",
        "*.py)",
        "*[!A-Za-z0-9_-]*) job=cron",
    )
    for snippet in required:
        if snippet not in text:
            _fail(
                "cron-run.sh のログ名の決め方が変わっています。"
                f"テストの対応を更新してください: {snippet}"
            )
    return match.group(1)


def _job_name(args: list[str]) -> tuple[str, str]:
    """cron-run.sh と同じ。最初の *.py の stem。使えない文字は cron。"""
    script = ""
    job = "cron"
    for arg in args:
        base = arg.rsplit("/", 1)[-1]
        if base.endswith(".py"):
            script = arg
            job = base[:-3]
            break
    if re.fullmatch(r"[A-Za-z0-9_-]+", job) is None:
        job = "cron"
    return job, script


def _strip_comment(line: str) -> str:
    quote: str | None = None
    for index, char in enumerate(line):
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "#":
            return line[:index]
    return line


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_words(line: str, start: int) -> tuple[list[str], int]:
    words: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    index = start
    limit = len(line)

    def flush() -> None:
        if buf:
            words.append("".join(buf))
            buf.clear()

    while index < limit:
        char = line[index]
        if quote is not None:
            if char == quote:
                quote = None
            else:
                buf.append(char)
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == "\\" and index + 1 < limit:
            buf.append(line[index + 1])
            index += 2
            continue
        if char in " \t":
            flush()
            index += 1
            continue
        if char in _SHELL_BREAKS:
            flush()
            break
        buf.append(char)
        index += 1
    else:
        flush()
    return words, index


def iter_simple_commands(line: str) -> list[list[str]]:
    commands: list[list[str]] = []
    index = 0
    limit = len(line)
    while index < limit:
        if line[index] in " \t" or line[index] in _SHELL_BREAKS:
            index += 1
            continue
        words, index = _read_words(line, index)
        if words:
            commands.append(words)
    return commands


def _is_cron_run_token(word: str) -> bool:
    return word.rsplit("/", 1)[-1] == "cron-run.sh"


def shell_commands(text: str) -> list[list[str]]:
    found: list[list[str]] = []
    extras: list[str] = []
    for line in text.splitlines():
        for words in iter_simple_commands(_strip_comment(line)):
            found.append(words)
            for word in words:
                if "cron-run.sh" in word and not _is_cron_run_token(word):
                    extras.append(word)
    for extra in extras:
        found.extend(iter_simple_commands(extra))
    return found


def _log_dir_from_prefix(words: list[str], default: str) -> str:
    log_dir = default
    for word in words:
        if word.startswith("CRON_LOG_DIR="):
            log_dir = word.split("=", 1)[1] or default
    return log_dir


def jobs_in_commands(
    commands: list[list[str]], log_dir: str, source: str
) -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, str, str]] = []
    for words in commands:
        for index, word in enumerate(words):
            if not _is_cron_run_token(word):
                continue
            name, script = _job_name(words[index + 1 :])
            used = _log_dir_from_prefix(words[:index], log_dir)
            found.append((name, f"{used}/{name}.log", script, source))
    return found


_APP_ROOT = "/home/stock/stock-analyzer/"


def _local_shell_script(token: str, deploy: Path) -> Path | None:
    """VPS 上の絶対パスか deploy/ 相対の .sh をリポジトリ内のファイルに戻す。"""
    repo = deploy.parent
    if token.startswith(_APP_ROOT):
        relative = token[len(_APP_ROOT) :]
    elif token.startswith("./"):
        relative = token[2:]
    elif token.startswith("deploy/"):
        relative = token
    else:
        return None
    if not relative.endswith(".sh") or relative.endswith("/cron-run.sh"):
        return None
    if Path(relative).name == "cron-run.sh":
        return None
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def jobs_from_crontab_text(
    text: str,
    log_dir: str,
    source: str,
    deploy: Path | None = None,
    seen: set[Path] | None = None,
) -> list[tuple[str, str, str, str]]:
    current = log_dir
    found: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        code = _strip_comment(line).strip()
        if not code:
            continue
        env_line = code[len("export ") :].strip() if code.startswith("export ") else code
        match = _ENV_LINE.match(env_line)
        if match is not None and "cron-run.sh" not in code:
            if match.group(1) == "CRON_LOG_DIR":
                current = _unquote(match.group(2)) or log_dir
            continue
        commands = shell_commands(code)
        found.extend(jobs_in_commands(commands, current, source))
        if deploy is None or seen is None:
            continue
        for words in commands:
            for word in words:
                path = _local_shell_script(word, deploy)
                if path is None:
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                found.extend(
                    jobs_from_crontab_text(
                        path.read_text(encoding="utf-8"),
                        current,
                        _source(path, deploy),
                        deploy,
                        seen,
                    )
                )
    return found


def crontab_script_stems(text: str) -> set[str]:
    stems: set[str] = set()
    for line in text.splitlines():
        code = _strip_comment(line).strip()
        if not code:
            continue
        for match in _JOB_RE.finditer(code):
            stems.add(match.group(1))
    return stems


def _source(path: Path, deploy: Path) -> str:
    try:
        return path.relative_to(deploy.parent).as_posix()
    except ValueError:
        return path.as_posix()


def _crontab_files(deploy: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(deploy.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name == "crontab" or name.endswith(".crontab"):
            found.append(path)
    return found


def _service_log_dir(text: str, default: str) -> str:
    log_dir = default
    for raw in text.splitlines():
        stripped = _strip_comment(raw).strip()
        if not stripped.lower().startswith("environment="):
            continue
        _, _, value = stripped.partition("=")
        value = _unquote(value)
        if value.startswith("CRON_LOG_DIR="):
            log_dir = value.split("=", 1)[1] or default
    return log_dir


def _timer_unit(timer: Path, text: str) -> str:
    match = re.search(r"(?im)^\s*unit\s*=\s*(\S+)", text)
    if match is not None:
        return match.group(1)
    return timer.name[: -len(".timer")] + ".service"


def jobs_from_timers(
    deploy: Path, log_dir: str, seen: set[Path]
) -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, str, str]] = []
    for timer in sorted(deploy.rglob("*.timer")):
        if not timer.is_file():
            continue
        text = timer.read_text(encoding="utf-8")
        if _TIMER_SCHEDULE.search(text) is None:
            continue
        unit = _timer_unit(timer, text)
        service = timer.parent / Path(unit).name
        if not service.is_file():
            _fail(f"{_source(timer, deploy)} が参照する {unit} が deploy にありません")
        body = service.read_text(encoding="utf-8")
        service_log_dir = _service_log_dir(body, log_dir)
        source = _source(service, deploy)
        for raw in body.splitlines():
            stripped = raw.strip()
            if stripped.startswith("#") or not stripped.lower().startswith("execstart"):
                continue
            _, _, value = stripped.partition("=")
            found.extend(
                jobs_from_crontab_text(
                    value.strip(), service_log_dir, source, deploy, seen
                )
            )
    return found


def _merge(rows: list[tuple[str, str, str, str]]) -> tuple[CronJob, ...]:
    acc: dict[tuple[str, str], dict[str, list[str]]] = {}
    for name, log_path, script, source in rows:
        slot = acc.setdefault((name, log_path), {"scripts": [], "sources": []})
        if script and script not in slot["scripts"]:
            slot["scripts"].append(script)
        if source not in slot["sources"]:
            slot["sources"].append(source)
    jobs = [
        CronJob(
            name,
            log_path,
            tuple(slot["scripts"]),
            tuple(slot["sources"]),
        )
        for (name, log_path), slot in acc.items()
    ]
    jobs.sort(key=lambda job: (job.name, job.log_path))
    return tuple(jobs)


def scheduled_cron_jobs(deploy: Path, log_dir: str) -> tuple[CronJob, ...]:
    rows: list[tuple[str, str, str, str]] = []
    seen: set[Path] = set()
    for path in _crontab_files(deploy):
        text = path.read_text(encoding="utf-8")
        rows.extend(
            jobs_from_crontab_text(text, log_dir, _source(path, deploy), deploy, seen)
        )
    rows.extend(jobs_from_timers(deploy, log_dir, seen))
    return _merge(rows)


def _split_clist(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class _TailBuild:
    def __init__(self) -> None:
        self.name = ""
        self.paths: list[str] = []
        self.excludes: list[str] = []


def tail_inputs(conf: str) -> tuple[TailInput, ...]:
    inputs: list[TailInput] = []
    current: _TailBuild | None = None

    def finish() -> None:
        nonlocal current
        if current is None:
            return
        if current.name == "tail":
            inputs.append(TailInput(tuple(current.paths), tuple(current.excludes)))
        current = None

    for raw in conf.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            finish()
            if stripped.lower() == "[input]":
                current = _TailBuild()
            continue
        if current is None:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        key = parts[0].lower()
        value = parts[1].strip()
        if key == "name":
            current.name = value.lower()
        elif key == "path":
            current.paths.extend(_split_clist(value))
        elif key == "exclude_path":
            current.excludes.extend(_split_clist(value))
    finish()
    return tuple(inputs)


def path_glob_matches(pattern: str, path: str) -> bool:
    """glibc glob。* と ? は / を跨がず、先頭の . はパターン側にも . が要る。"""
    pat_parts = pattern.split("/")
    path_parts = path.split("/")
    if len(pat_parts) != len(path_parts):
        return False
    for pat, segment in zip(pat_parts, path_parts):
        if segment.startswith(".") and not pat.startswith("."):
            return False
        if not fnmatch.fnmatchcase(segment, pat):
            return False
    return True


def exclude_matches(pattern: str, path: str) -> bool:
    """Fluent Bit の Exclude_Path。fnmatch(pattern, path, 0) なので * は / にマッチする。"""
    return fnmatch.fnmatchcase(path, pattern)


def log_is_tailed(path: str, inputs: tuple[TailInput, ...]) -> bool:
    for item in inputs:
        if not any(path_glob_matches(pattern, path) for pattern in item.paths):
            continue
        if any(exclude_matches(pattern, path) for pattern in item.excludes):
            continue
        return True
    return False


def _check_exclusions(jobs: tuple[CronJob, ...], excluded: dict[str, str]) -> None:
    known = {job.name for job in jobs}
    problems: list[str] = []
    for name in sorted(excluded):
        reason = excluded[name]
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"{name}: 理由が空です")
        if name not in known:
            problems.append(f"{name}: crontab / timer に無いジョブです")
    if problems:
        detail = "\n".join(f"  {line}" for line in problems)
        _fail(f"除外リストを見直してください:\n{detail}")


def cron_log_gaps(
    jobs: tuple[CronJob, ...], conf: str, excluded: dict[str, str]
) -> tuple[CoverageGap, ...]:
    _check_exclusions(jobs, excluded)
    inputs = tail_inputs(conf)
    gaps: list[CoverageGap] = []
    for job in jobs:
        if job.name in excluded:
            continue
        if log_is_tailed(job.log_path, inputs):
            continue
        gaps.append(CoverageGap(job.name, job.log_path, job.sources))
    return tuple(gaps)


def format_gaps(gaps: tuple[CoverageGap, ...]) -> str:
    lines = ["Fluent Bit が cron ジョブのログを拾っていません:"]
    for gap in gaps:
        sources = ", ".join(gap.sources)
        lines.append(f"  {gap.name} ({gap.log_path}) 定義: {sources}")
    return "\n".join(lines)


def test_cron_run_log_dir_contract() -> None:
    log_dir = cron_run_log_dir(ROOT / "deploy" / "cron-run.sh")
    if log_dir != "/var/log/app":
        _fail(f"cron-run.sh の既定ログディレクトリが変わりました: {log_dir}")


def test_job_name_follows_cron_run() -> None:
    name, script = _job_name(["jobs/xbrl/tdnet_xbrl_r2.py", "2"])
    if (name, script) != ("tdnet_xbrl_r2", "jobs/xbrl/tdnet_xbrl_r2.py"):
        _fail(f"最初の .py を使う: {(name, script)}")
    name, script = _job_name(["jobs/a.py", "jobs/b.py"])
    if name != "a":
        _fail(f"最初の .py で止める: {name}")
    name, script = _job_name(["jobs/foo.bar.py"])
    if name != "cron" or script != "jobs/foo.bar.py":
        _fail(f"使えない文字は cron.log: {(name, script)}")
    name, _script = _job_name(["--help"])
    if name != "cron":
        _fail(f".py が無ければ cron: {name}")


def test_crontab_chain_comments_and_log_dir() -> None:
    chain = (
        "0 12-23 * * * cd /app && { "
        "deploy/cron-run.sh jobs/xbrl/tdnet_xbrl_r2.py 2; "
        "deploy/cron-run.sh jobs/xbrl/xbrl_parse.py; "
        "deploy/cron-run.sh jobs/xbrl/xbrl_normalize.py; }"
    )
    text = f"""\
# deploy/cron-run.sh jobs/ops/commented.py
CRON_TZ=Asia/Tokyo
SHELL=/bin/bash
30 19 * * * cd /app && deploy/cron-run.sh jobs/market/run_batch.py # daily
{chain}
CRON_LOG_DIR=/tmp/custom
0 1 * * * deploy/cron-run.sh jobs/fx_rates.py
CRON_LOG_DIR=
0 2 * * * CRON_LOG_DIR=/tmp/once deploy/cron-run.sh jobs/ops/backup_db.py
0 3 * * * deploy/cron-run.sh jobs/ops/backup_data.py
"""
    rows = jobs_from_crontab_text(text, "/var/log/app", "deploy/crontab")
    got = {(name, path) for name, path, _script, _source in rows}
    expected = {
        ("run_batch", "/var/log/app/run_batch.log"),
        ("tdnet_xbrl_r2", "/var/log/app/tdnet_xbrl_r2.log"),
        ("xbrl_parse", "/var/log/app/xbrl_parse.log"),
        ("xbrl_normalize", "/var/log/app/xbrl_normalize.log"),
        ("fx_rates", "/tmp/custom/fx_rates.log"),
        ("backup_db", "/tmp/once/backup_db.log"),
        ("backup_data", "/var/log/app/backup_data.log"),
    }
    if got != expected:
        _fail(f"crontab の解釈が違う: {sorted(got)}")
    if "commented" in {name for name, _path, _script, _source in rows}:
        _fail("コメント行のジョブを拾っている")
    stems = crontab_script_stems(text)
    if "commented" in stems or "run_batch" not in stems:
        _fail(f"stem 抽出が違う: {sorted(stems)}")


def test_timer_execstart_is_a_cron_definition(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "crontab").write_text(
        "30 19 * * * cd /app && deploy/cron-run.sh jobs/market/run_batch.py\n",
        encoding="utf-8",
    )
    (deploy / "extra.timer").write_text(
        "[Timer]\nOnCalendar=daily\nUnit=extra.service\n",
        encoding="utf-8",
    )
    (deploy / "extra.service").write_text(
        "[Service]\n"
        "Environment=CRON_LOG_DIR=/var/log/custom\n"
        'ExecStart=/bin/bash -lc "deploy/cron-run.sh jobs/ops/extra_job.py"\n',
        encoding="utf-8",
    )
    (deploy / "watch.timer").write_text(
        "[Timer]\nOnUnitActiveSec=5min\nUnit=watch.service\n",
        encoding="utf-8",
    )
    (deploy / "watch.service").write_text(
        "[Service]\nExecStart=/bin/true\n",
        encoding="utf-8",
    )
    (deploy / "web.service").write_text(
        '[Service]\nExecStart=/bin/bash -lc "deploy/cron-run.sh jobs/ops/not_cron.py"\n',
        encoding="utf-8",
    )
    (deploy / "wrap.timer").write_text(
        "[Timer]\nOnCalendar=daily\nUnit=wrap.service\n",
        encoding="utf-8",
    )
    (deploy / "wrap.service").write_text(
        "[Service]\n"
        "ExecStart=/home/stock/stock-analyzer/deploy/wrap.sh\n",
        encoding="utf-8",
    )
    (deploy / "wrap.sh").write_text(
        "#!/bin/bash\n"
        "# deploy/cron-run.sh jobs/ops/commented.py\n"
        "export CRON_LOG_DIR=/var/log/wrap\n"
        "deploy/cron-run.sh jobs/ops/wrapped.py\n",
        encoding="utf-8",
    )
    (deploy / "unused.sh").write_text(
        "deploy/cron-run.sh jobs/ops/unused.py\n",
        encoding="utf-8",
    )
    jobs = {job.name: job for job in scheduled_cron_jobs(deploy, "/var/log/app")}
    if set(jobs) != {"run_batch", "extra_job", "wrapped"}:
        _fail(f"timer のジョブだけを足す: {sorted(jobs)}")
    wrapped = jobs["wrapped"]
    if wrapped.log_path != "/var/log/wrap/wrapped.log":
        _fail(f"wrapper の CRON_LOG_DIR を使う: {wrapped.log_path}")
    if wrapped.sources != ("deploy/wrap.sh",):
        _fail(f"定義元は wrapper: {wrapped.sources}")
    extra = jobs["extra_job"]
    if extra.log_path != "/var/log/custom/extra_job.log":
        _fail(f"service の CRON_LOG_DIR を使う: {extra.log_path}")
    if extra.sources != ("deploy/extra.service",):
        _fail(f"定義元は service: {extra.sources}")


def test_path_glob_and_exclude_match_fluent_bit() -> None:
    if not path_glob_matches("/var/log/app/*.log", "/var/log/app/run_batch.log"):
        _fail("ディレクトリ直下の *.log はマッチする")
    if path_glob_matches("/var/log/app/*.log", "/var/log/app/sub/run_batch.log"):
        _fail("* は / を跨がない")
    if path_glob_matches("/var/log/*.log", "/var/log/app/run_batch.log"):
        _fail("親の *.log は一段下のファイルにマッチしない")
    if path_glob_matches("*.log", "/var/log/app/run_batch.log"):
        _fail("相対パターンは絶対パスにマッチしない")
    if path_glob_matches("/var/log/app/*.log", "/var/log/app/.hidden.log"):
        _fail("* は先頭が . のファイルにマッチしない")
    if not path_glob_matches("/var/log/app/.*.log", "/var/log/app/.hidden.log"):
        _fail("パターン側の . は隠しファイルにマッチする")
    if not exclude_matches("*.1", "/var/log/app/run_batch.log.1"):
        _fail("Exclude_Path *.1 はローテート後のパスにマッチする")
    if exclude_matches("*.1", "/var/log/app/intraday_1m.log"):
        _fail("Exclude_Path *.1 は intraday_1m.log を落とさない")
    if exclude_matches("*.gz", "/var/log/app/run_batch.log"):
        _fail("Exclude_Path *.gz は live の .log を落とさない")
    conf = """\
[INPUT]
    Name tail
    Path /var/log/app/run_batch.log, /var/log/app/fx_rates.log
    Exclude_Path /var/log/app/fx_rates.log, *.gz
[INPUT]
    Name systemd
    Path /var/log/app/ignored.log
[INPUT]
    Name tail
    Path /var/log/app/*.log
    Exclude_Path /var/log/app/run_batch.log,/var/log/app/fx_rates.log
[INPUT]
    Name tail
    Path /var/log/app/run_batch.log
"""
    inputs = tail_inputs(conf)
    if len(inputs) != 3:
        _fail(f"tail 以外の INPUT は数えない: {inputs}")
    if not log_is_tailed("/var/log/app/run_batch.log", inputs):
        _fail("別の tail が拾っていれば対象")
    if log_is_tailed("/var/log/app/fx_rates.log", inputs):
        _fail("全 tail で除外されたファイルは対象外")
    if not log_is_tailed("/var/log/app/backup_db.log", inputs):
        _fail("*.log が拾うファイルは対象")


def test_exclusion_list_needs_a_reason_and_a_scheduled_job() -> None:
    jobs = (
        CronJob(
            "run_batch",
            "/var/log/app/run_batch.log",
            ("jobs/market/run_batch.py",),
            ("deploy/crontab",),
        ),
    )
    covered = "[INPUT]\n    Name tail\n    Path /var/log/app/*.log\n"
    try:
        cron_log_gaps(jobs, covered, {"run_batch": "   "})
    except SystemExit as exc:
        if "run_batch" not in str(exc) or "理由が空" not in str(exc):
            _fail(str(exc))
    else:
        _fail("空の理由は失敗させる")
    try:
        cron_log_gaps(jobs, covered, {"missing": "手で見る"})
    except SystemExit as exc:
        if "missing" not in str(exc):
            _fail(str(exc))
    else:
        _fail("スケジュールに無い除外は失敗させる")
    missed = "[INPUT]\n    Name tail\n    Path /var/log/nginx/error.log\n"
    gaps = cron_log_gaps(jobs, missed, {"run_batch": "別経路で通知する"})
    if gaps:
        _fail(f"理由つきの除外は欠落に数えない: {gaps}")


def test_gap_message_names_the_job() -> None:
    jobs = (
        CronJob(
            "run_batch",
            "/var/log/app/run_batch.log",
            ("jobs/market/run_batch.py",),
            ("deploy/crontab",),
        ),
        CronJob(
            "fx_rates",
            "/var/log/app/fx_rates.log",
            ("jobs/fx_rates.py",),
            ("deploy/crontab",),
        ),
    )
    conf = "[INPUT]\n    Name tail\n    Path /var/log/app/run_batch.log\n"
    gaps = cron_log_gaps(jobs, conf, {})
    if tuple(gap.name for gap in gaps) != ("fx_rates",):
        _fail(f"漏れたジョブだけを返す: {gaps}")
    text = format_gaps(gaps)
    if "fx_rates" not in text or "/var/log/app/fx_rates.log" not in text:
        _fail(text)
    if "run_batch" in text:
        _fail(f"拾えているジョブを欠落に出している: {text}")


def test_repo_cron_logs_are_tailed() -> None:
    deploy = ROOT / "deploy"
    log_dir = cron_run_log_dir(deploy / "cron-run.sh")
    text = (deploy / "crontab").read_text(encoding="utf-8")
    parsed = jobs_from_crontab_text(text, log_dir, "deploy/crontab")
    parsed_names = {name for name, _path, _script, _source in parsed}
    stems = crontab_script_stems(text)
    if parsed_names != stems:
        only_parser = sorted(parsed_names - stems)
        only_scripts = sorted(stems - parsed_names)
        _fail(
            "crontab の jobs/*.py と cron-run.sh のログ名が一致しません: "
            f"parser={only_parser} scripts={only_scripts}"
        )
    jobs = scheduled_cron_jobs(deploy, log_dir)
    scheduled_names = {job.name for job in jobs}
    if not stems <= scheduled_names:
        missing = sorted(stems - scheduled_names)
        _fail(f"crontab のジョブがスケジュール一覧から落ちています: {missing}")
    by_name = {job.name: job for job in jobs}
    if "run_batch" not in by_name:
        _fail(f"run_batch が見つかりません: {sorted(by_name)}")
    if by_name["run_batch"].log_path != f"{log_dir}/run_batch.log":
        _fail(by_name["run_batch"].log_path)
    for name in ("tdnet_xbrl_r2", "xbrl_parse", "xbrl_normalize"):
        if name not in by_name:
            _fail(f"連鎖 cron の {name} が見つかりません")
    conf_path = deploy / "fluent-bit.conf"
    if not conf_path.is_file():
        _fail("deploy/fluent-bit.conf がありません")
    gaps = cron_log_gaps(jobs, conf_path.read_text(encoding="utf-8"), EXCLUDED_CRON_JOBS)
    if gaps:
        _fail(format_gaps(gaps))


def test_removing_batch_glob_names_the_missing_jobs() -> None:
    """設定の一括 Path を1ファイルに狭めると、外れたジョブ名で失敗する。"""
    if "run_batch" in EXCLUDED_CRON_JOBS:
        _fail("run_batch を除外したなら、このテストの対象を変えてください")
    deploy = ROOT / "deploy"
    log_dir = cron_run_log_dir(deploy / "cron-run.sh")
    jobs = scheduled_cron_jobs(deploy, log_dir)
    conf = (deploy / "fluent-bit.conf").read_text(encoding="utf-8")
    token = "/var/log/app/*.log"
    if conf.count(token) != 1:
        _fail(f"batch の Path glob が1つではありません: {conf.count(token)}")
    if cron_log_gaps(jobs, conf, EXCLUDED_CRON_JOBS):
        _fail("直す前の設定に漏れがある")
    broken = conf.replace(token, "/var/log/app/fx_rates.log", 1)
    gaps = cron_log_gaps(jobs, broken, EXCLUDED_CRON_JOBS)
    names = {gap.name for gap in gaps}
    if "run_batch" not in names:
        _fail(f"glob を外すと run_batch が漏れる: {sorted(names)}")
    if "fx_rates" in names:
        _fail("残した fx_rates.log まで欠落になっている")
    text = format_gaps(gaps)
    if "run_batch" not in text or "/var/log/app/run_batch.log" not in text:
        _fail(text)


def main() -> None:
    from tempfile import TemporaryDirectory

    test_cron_run_log_dir_contract()
    test_job_name_follows_cron_run()
    test_crontab_chain_comments_and_log_dir()
    with TemporaryDirectory() as directory:
        test_timer_execstart_is_a_cron_definition(Path(directory))
    test_path_glob_and_exclude_match_fluent_bit()
    test_exclusion_list_needs_a_reason_and_a_scheduled_job()
    test_gap_message_names_the_job()
    test_repo_cron_logs_are_tailed()
    test_removing_batch_glob_names_the_missing_jobs()
    print("ok: fluent-bit cron logs")


if __name__ == "__main__":
    main()
