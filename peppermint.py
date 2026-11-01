#!/usr/bin/env python3
"""
Peppermint - compiles PocketMine-MP.phar from a PocketMine-MP git repository
(made for https://github.com/NetherGamesMC/PocketMine-MP, works with pmmp and other forks).

Pipeline: git clone -> composer install --no-dev --classmap-authoritative
          -> php -dphar.readonly=0 build/server-phar.php --out <file>

Only the Python standard library is used. Needs git and a PocketMine PHP binary
(the one in your server's bin/php folder is found automatically). Composer is downloaded if missing.
"""
from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_REPO = "https://github.com/NetherGamesMC/PocketMine-MP"
COMPOSER_URL = "https://getcomposer.org/download/latest-stable/composer.phar"
CACHE_DIR = HERE / ".cache"
WORK_DIR = HERE / ".work"
LOG_DIR = HERE / "logs"
BUILD_DIR = HERE / "builds"

# ---------------------------------------------------------------- terminal / colours

ESC = "\x1b["
RESET, BOLD, DIM = ESC + "0m", ESC + "1m", ESC + "2m"
HIDE, SHOW, CLEAR_LINE = ESC + "?25l", ESC + "?25h", ESC + "K"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

CANE_RED, CANE_WHITE, CANE_GREEN = (220, 30, 48), (250, 250, 250), (38, 166, 91)
# candy cane: red/white stripes of equal width, with one thin mint-green stripe per cycle
STRIPES = [(CANE_RED, 1.0), (CANE_WHITE, 1.0)] * 4 + [(CANE_RED, 1.0), (CANE_WHITE, 0.6), (CANE_GREEN, 0.4), (CANE_WHITE, 0.6)]
STRIPES_TOTAL = sum(w for _, w in STRIPES)
EDGE = 0.18  # part of a stripe that blends into the next one, so edges look smooth instead of jagged


def rgb(c: tuple[int, int, int]) -> str:
    return f"{ESC}38;2;{c[0]};{c[1]};{c[2]}m"


def gradient(t: float) -> tuple[int, int, int]:
    """Candy cane colour at position t (cyclic, t can be any float)."""
    pos = (t % 1.0) * STRIPES_TOTAL
    for i, (color, width) in enumerate(STRIPES):
        if pos < width:
            f = pos / width
            if f <= 1 - EDGE:
                return color
            nxt = STRIPES[(i + 1) % len(STRIPES)][0]
            k = (f - (1 - EDGE)) / EDGE
            return tuple(int(color[j] + (nxt[j] - color[j]) * k) for j in range(3))  # type: ignore[return-value]
        pos -= width
    return STRIPES[-1][0]


GREEN, RED, SNOW = rgb(CANE_GREEN), rgb(CANE_RED), rgb(CANE_WHITE)
PINK = rgb((255, 150, 165))  # warnings: peppermint pink, clearly not the error red
GREY = rgb((170, 160, 162))
SHADOW = rgb((105, 18, 30))  # dark cherry under the logo
ACCENT = RED

# icons: plain Unicode symbols, none of them have an emoji form
OK, FAIL, WARN, ARROW, DOT = "✓", "✗", "▲", "›", "•"


def out(s: str = "", end: str = "\n") -> None:
    sys.stdout.write(s + end)
    sys.stdout.flush()


def visible_len(s: str) -> int:
    return len(ANSI_RE.sub("", s))


def term_width() -> int:
    return max(40, shutil.get_terminal_size((100, 30)).columns)


def setup_terminal() -> None:
    if os.name == "nt":
        os.system("")  # turns on ANSI escape processing in the classic Windows console
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------- animated banner

FONT = {
    "P": ["████ ", "█   █", "████ ", "█    ", "█    "],
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "B": ["████ ", "█   █", "████ ", "█   █", "████ "],
    "U": ["█   █", "█   █", "█   █", "█   █", " ███ "],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "M": ["█   █", "██ ██", "█ █ █", "█   █", "█   █"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    " ": ["  ", "  ", "  ", "  ", "  "],
}


def render_text(text: str) -> list[str]:
    rows = [""] * 5
    for ch in text:
        for i, part in enumerate(FONT[ch]):
            rows[i] += part + " "
    return rows


def with_shadow(rows: list[str]) -> list[str]:
    """Adds a 1-block drop shadow (down-right) drawn with a shade character."""
    h, w = len(rows) + 1, max(len(r) for r in rows) + 1
    grid = [[" "] * w for _ in range(h)]
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != " " and grid[y + 1][x + 1] == " ":
                grid[y + 1][x + 1] = "░"
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != " ":
                grid[y][x] = ch
    return ["".join(r).rstrip() for r in grid]


def paint(rows: list[str], phase: float, upto: int | None = None) -> str:
    lines = []
    for y, row in enumerate(rows):
        parts = []
        for x, ch in enumerate(row if upto is None else row[:upto]):
            if ch == " ":
                parts.append(" ")
            elif ch == "░":
                parts.append(SHADOW + ch + RESET)
            else:
                # x + 2y makes the stripes diagonal (characters are about twice as tall as wide)
                parts.append(rgb(gradient((x + y * 2) * 0.02 + phase)) + ch)
        lines.append("  " + "".join(parts) + RESET + CLEAR_LINE)
    return "\n".join(lines)


def intro(animate: bool) -> None:
    rows = with_shadow(render_text("PEPPERMINT"))
    if max(len(r) for r in rows) + 4 > term_width():
        rows = ["PEPPERMINT"]  # terminal too narrow for the big logo
    width = max(len(r) for r in rows)
    subtitle = "PocketMine-MP .phar compiler"
    out()
    if not animate:
        out(paint(rows, 0.0))
        out("  " + SNOW + subtitle + RESET + "\n")
        return
    out(HIDE, end="")
    # 1) reveal the logo left to right
    for col in range(0, width + 1, 2):
        out(paint(rows, col * 0.01, upto=col))
        out(f"{ESC}{len(rows)}A", end="")
        time.sleep(0.012)
    # 2) roll the stripes along, like a turning candy cane
    for frame in range(50):
        out(paint(rows, 0.3 + frame * 0.012))
        out(f"{ESC}{len(rows)}A", end="")
        time.sleep(0.022)
    out(paint(rows, 0.3 + 50 * 0.012))
    # 3) type the subtitle: white letters, red cursor
    out("  ", end="")
    for ch in subtitle:
        out(SNOW + ch + RED + "▌" + RESET + "\b", end="")
        time.sleep(0.012)
    out(" ", end="")
    out("\n")
    out(SHOW, end="")


# ---------------------------------------------------------------- UI pieces

def rule(title: str) -> None:
    w = min(term_width() - 4, 78)
    label = f" {title} "
    left = 3
    right = max(0, w - left - len(label))
    bar = lambda start, n: "".join(rgb(gradient((start + i) * 0.025)) + "━" for i in range(n))
    out("  " + bar(0, left) + SNOW + BOLD + label + RESET + bar(left + len(label), right) + RESET)


def ask(label: str, default: str = "") -> str:
    hint = f" {DIM}[{default}]{RESET}" if default else ""
    out(SHOW, end="")
    try:
        value = input(f"  {ACCENT}{ARROW}{RESET} {BOLD}{label}{RESET}{hint}: ")
    except EOFError:
        value = ""
    return value.strip().strip('"').strip("'") or default


def confirm(label: str, default: bool = False) -> bool:
    value = ask(label + (" (Y/n)" if default else " (y/N)")).lower()
    return default if value == "" else value in ("y", "yes")


def box(lines: list[str], color: str | None = None) -> None:
    """A rounded box. Without a colour the border is a candy cane stripe."""
    w = max(visible_len(l) for l in lines) + 2
    paint_at = (lambda i: color) if color else (lambda i: rgb(gradient(i * 0.025)))
    edge = lambda chars, start: "".join(paint_at(start + i) + c for i, c in enumerate(chars))
    out("  " + edge("╭" + "─" * w + "╮", 0) + RESET)
    for r, l in enumerate(lines, 1):
        # sides use the row number twice so the stripes keep their diagonal going down the box
        out("  " + paint_at(r * 2) + "│" + RESET + " " + l + " " * (w - 1 - visible_len(l)) + paint_at(w + 1 + r * 2) + "│" + RESET)
    out("  " + edge("╰" + "─" * w + "╯", (len(lines) + 1) * 2) + RESET)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class BuildError(Exception):
    def __init__(self, message: str, tail: list[str] | None = None):
        super().__init__(message)
        self.tail = tail or []


class Task:
    """One step on screen: an animated spinner while it runs, then a tick or a cross."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, title: str):
        self.title = title
        self.detail = ""
        self.start = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Task":
        self.start = time.monotonic()
        if sys.stdout.isatty():  # piped output (logs, CI) gets only the final line
            out(HIDE, end="")
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def _spin(self) -> None:
        i = 0
        while not self._stop.wait(0.08):
            icon = rgb(gradient(i * 0.02)) + self.FRAMES[i % len(self.FRAMES)] + RESET
            self._line(icon)
            i += 1

    def _line(self, icon: str, final: bool = False) -> None:
        elapsed = time.monotonic() - self.start
        head = f"  {icon} {BOLD}{self.title}{RESET} {GREY}{elapsed:.1f}s{RESET}"
        detail = ANSI_RE.sub("", self.detail).strip()
        room = term_width() - visible_len(head) - 4
        if detail and not final and room > 12:
            if len(detail) > room:
                detail = detail[: room - 1] + "…"
            head += f"  {DIM}{detail}{RESET}"
        out("\r" + head + CLEAR_LINE, end="")

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._line(GREEN + OK + RESET if exc_type is None else RED + FAIL + RESET, final=True)
        out(SHOW)
        return False


def run(cmd: list[str], task: Task, log, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    """Runs a command, streams its output into the log and the spinner's detail text."""
    log.write(f"\n$ {' '.join(cmd)}\n")
    log.flush()
    tail: deque[str] = deque(maxlen=18)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except FileNotFoundError:
        raise BuildError(f"{cmd[0]} not found")
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            log.write(raw)
            for part in re.split(r"[\r\n]+", raw):
                part = ANSI_RE.sub("", part).strip()
                if part:
                    task.detail = part
                    tail.append(part)
    except KeyboardInterrupt:
        proc.kill()
        raise
    code = proc.wait()
    log.flush()
    if code != 0:
        raise BuildError(f"{Path(cmd[0]).name} failed (exit {code})", list(tail))


# ---------------------------------------------------------------- build steps

def parse_repo(link: str) -> tuple[str, str | None]:
    """Accepts https URLs, github.com/owner/repo, owner/repo, .../tree/<branch> links, git@ URLs and local paths."""
    link = link.strip()
    if re.fullmatch(r"[\w.-]+/[\w.-]+", link) and not Path(link).exists():
        link = "https://github.com/" + link
    if link.startswith("github.com/"):
        link = "https://" + link
    m = re.fullmatch(r"(https?://github\.com/[\w.-]+/[\w.-]+?)(?:\.git)?(?:/tree/(.+?))?/?", link)
    if m:
        return m.group(1), m.group(2)
    if re.match(r"(https?://|git@|ssh://)", link) or Path(link).exists():
        return link, None
    raise ValueError(f"Not a git link: {link}")


def find_php(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("PHP_BINARY"):
        candidates.append(Path(os.environ["PHP_BINARY"]))
    exe = "php.exe" if os.name == "nt" else "bin/php"
    for base in (HERE, HERE.parent / "server", HERE.parent):
        candidates += [base / "bin" / "php" / exe, base / "bin" / exe]
    which = shutil.which("php")
    if which:
        candidates.append(Path(which))
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise BuildError("PHP not found. Use --php <path to php.exe>")


def check_php(php: Path, task: Task) -> str:
    version = subprocess.run([str(php), "-r", "echo PHP_VERSION;"], capture_output=True, text=True).stdout.strip()
    if not re.match(r"\d+\.\d+", version) or tuple(int(x) for x in version.split(".")[:2]) < (8, 1):
        raise BuildError(f"Needs PHP 8.1+, found {version or 'nothing'}")
    modules = subprocess.run([str(php), "-m"], capture_output=True, text=True).stdout.lower()
    missing = [m for m in ("phar", "pmmpthread", "yaml", "chunkutils2", "igbinary", "leveldb", "morton", "zip")
               if m not in modules.split()]
    if missing:
        raise BuildError(f"PHP is missing: {', '.join(missing)}. Use the PocketMine PHP binary")
    task.detail = f"PHP {version}"
    return version


def _ext_key(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name.lower())


def missing_extensions(php: Path, workdir: Path) -> list[str]:
    """PHP extensions the repo's composer.json / composer.lock ask for that this PHP doesn't have."""
    wanted: set[str] = set()
    try:
        wanted |= {k[4:] for k in json.loads((workdir / "composer.json").read_text("utf-8")).get("require", {}) if k.startswith("ext-")}
        lock = workdir / "composer.lock"
        if lock.is_file():
            for pkg in json.loads(lock.read_text("utf-8")).get("packages", []):
                wanted |= {k[4:] for k in pkg.get("require", {}) if k.startswith("ext-")}
    except (OSError, json.JSONDecodeError):
        return []
    res = subprocess.run([str(php), "-r", "echo json_encode(get_loaded_extensions());"], capture_output=True, text=True)
    try:
        loaded = {_ext_key(e) for e in json.loads(res.stdout)}
    except json.JSONDecodeError:
        return []
    return sorted(e for e in wanted if _ext_key(e) not in loaded)


def remote_default_branch(repo: str) -> str | None:
    try:
        res = subprocess.run(["git", "ls-remote", "--symref", repo, "HEAD"], capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", res.stdout)
    if res.returncode != 0:
        raise BuildError("Repository not reachable. Check the link", res.stderr.strip().splitlines()[-5:])
    return m.group(1) if m else None


def get_composer(task: Task, refresh: bool) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / "composer.phar"
    if target.is_file() and not refresh:
        task.detail = "cached"
        return target
    headers = {"User-Agent": "peppermint"}
    with urllib.request.urlopen(urllib.request.Request(COMPOSER_URL + ".sha256", headers=headers), timeout=60) as r:
        expected = r.read().decode().split()[0].strip().lower()
    tmp = target.with_suffix(".download")
    digest = hashlib.sha256()
    with urllib.request.urlopen(urllib.request.Request(COMPOSER_URL, headers=headers), timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(65536):
            f.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            task.detail = f"{human_size(done)}" + (f" / {human_size(total)}" if total else "")
    if digest.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        raise BuildError("composer.phar checksum mismatch. Run again")
    tmp.replace(target)
    return target


def force_rmtree(path: Path) -> None:
    def make_writable(func, p, *_):  # git marks object files read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def repo_slug(repo: str, branch: str | None) -> str:
    name = re.sub(r"\.git$", "", repo.rstrip("/\\")).replace("\\", "/").split("/")[-2:]
    return re.sub(r"[^\w.-]+", "_", "-".join(name + ([branch] if branch else [])))


def read_phar_metadata(php: Path, phar: Path) -> dict:
    code = "echo json_encode((new Phar($argv[1]))->getMetadata());"
    res = subprocess.run([str(php), "-r", code, str(phar)], capture_output=True, text=True)
    try:
        data = json.loads(res.stdout)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_name(path.name + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    path.replace(bak)
    return bak


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Compile PocketMine-MP.phar from a PocketMine-MP git repository.")
    ap.add_argument("--repo", help=f"repository link (default asks, suggestion: {DEFAULT_REPO})")
    ap.add_argument("--branch", help="branch or tag (default: the repository's default branch)")
    ap.add_argument("--out", help="output .phar path (default: builds/PocketMine-MP.phar)")
    ap.add_argument("--php", help="path to the PHP binary (default: your server's bin/php is searched)")
    ap.add_argument("--yes", "-y", action="store_true", help="don't ask, use the defaults / arguments")
    ap.add_argument("--no-anim", action="store_true", help="skip the intro animation")
    ap.add_argument("--update-composer", action="store_true", help="download composer.phar again")
    ap.add_argument("--keep-source", action="store_true", help="keep the cloned source in .work/ after building")
    args = ap.parse_args()

    setup_terminal()
    interactive = sys.stdin.isatty() and not args.yes
    intro(animate=sys.stdout.isatty() and not args.no_anim)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"build-{datetime.now():%Y%m%d-%H%M%S}.log"
    started = time.monotonic()
    workdir: Path | None = None

    with open(log_path, "w", encoding="utf-8") as log:
        try:
            # ---- settings
            rule("SETUP")
            link = args.repo or (ask("Repository", DEFAULT_REPO) if interactive else DEFAULT_REPO)
            try:
                repo, branch = parse_repo(link)
            except ValueError as e:
                raise BuildError(str(e))
            if args.branch:
                branch = args.branch

            with Task("Repository") as t:
                t.detail = repo
                default_branch = remote_default_branch(repo)
            if branch is None and interactive:
                branch = ask("Branch", default_branch or "") or None
            branch = branch or default_branch

            default_out = Path(args.out) if args.out else BUILD_DIR / "PocketMine-MP.phar"
            out_path = Path(ask("Output", str(default_out)) if interactive and not args.out else default_out)
            if out_path.suffix.lower() != ".phar":
                out_path = out_path / "PocketMine-MP.phar" if out_path.is_dir() or not out_path.suffix else out_path.with_suffix(".phar")
            out_path = out_path.resolve()
            out()
            box([f"{GREY}Repo   {RESET} {repo}",
                 f"{GREY}Branch {RESET} {branch or 'default'}",
                 f"{GREY}Output {RESET} {out_path}"])
            if interactive and not confirm("Build", default=True):
                out(f"\n  {PINK}{WARN} Cancelled{RESET}")
                return 0
            out()

            # ---- build
            rule("BUILD")
            with Task("PHP") as t:
                php = find_php(args.php)
                check_php(php, t)
                t.title = f"PHP {GREY}{php}{RESET}"
            with Task("Composer") as t:
                composer = get_composer(t, args.update_composer)

            workdir = WORK_DIR / repo_slug(repo, branch)
            if workdir.exists():
                force_rmtree(workdir)
            workdir.parent.mkdir(parents=True, exist_ok=True)
            with Task("Clone") as t:
                cmd = ["git", "clone", "--depth", "1", "--progress"]
                if branch:
                    cmd += ["--branch", branch]
                run(cmd + [repo, str(workdir)], t, log)
            if not (workdir / "build" / "server-phar.php").is_file() or not (workdir / "composer.json").is_file():
                raise BuildError("No build/server-phar.php. Not a PocketMine-MP repository")
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=workdir, capture_output=True, text=True).stdout.strip()
            version_info = workdir / "src" / "VersionInfo.php"
            m = re.search(r'BASE_VERSION\s*=\s*"([^"]+)"', version_info.read_text("utf-8")) if version_info.is_file() else None
            source_version = m.group(1) if m else None

            env = dict(os.environ, COMPOSER_NO_INTERACTION="1")
            if os.environ.get("GITHUB_TOKEN") and "COMPOSER_AUTH" not in env:  # avoids GitHub's download rate limit
                env["COMPOSER_AUTH"] = json.dumps({"github-oauth": {"github.com": os.environ["GITHUB_TOKEN"]}})
            # extensions are only needed to RUN the server, not to build the phar, so missing ones are skipped
            # here and reported at the end
            missing = missing_extensions(php, workdir)
            with Task("Dependencies") as t:
                run([str(php), str(composer), "install", "--no-dev", "--classmap-authoritative", "--prefer-dist",
                     "--no-interaction", "--ansi"] + [f"--ignore-platform-req=ext-{e}" for e in missing], t, log, cwd=workdir, env=env)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            old = backup(out_path)
            with Task("Compile") as t:
                run([str(php), "-dphar.readonly=0", str(workdir / "build" / "server-phar.php"), "--out", str(out_path)],
                    t, log, cwd=workdir)
            if not out_path.is_file():
                raise BuildError("No .phar was written")

            with Task("Verify") as t:
                meta = read_phar_metadata(php, out_path)
                if not meta:
                    raise BuildError("PHP can't open the .phar, it is broken")

            # ---- summary
            out()
            rule("DONE")
            lines = [f"{GREEN}{BOLD}{OK}{RESET} {SNOW}{BOLD}Built in {time.monotonic() - started:.0f}s{RESET}", ""]
            if source_version and not meta.get("version"):
                meta["version"] = source_version
            for key, label in (("name", "Name"), ("version", "Version"), ("api", "API"), ("minecraft", "Minecraft"),
                               ("protocol", "Protocol"), ("git", "Git")):
                if meta.get(key) not in (None, ""):
                    lines.append(f"{GREY}{label:<10}{RESET} {meta[key]}")
            if commit:
                lines.append(f"{GREY}{'Commit':<10}{RESET} {commit} ({branch or 'default'})")
            lines += [f"{GREY}{'Size':<10}{RESET} {human_size(out_path.stat().st_size)}",
                      f"{GREY}{'File':<10}{RESET} {out_path}"]
            if old:
                lines.append(f"{GREY}{'Old file':<10}{RESET} {old.name}")
            box(lines)
            if missing:
                out()
                box([f"{PINK}{BOLD}{WARN} Missing PHP extension: {', '.join(missing)}{RESET}",
                     f"{GREY}Skipped for the build. Features that need it stay off on this PHP.{RESET}"], PINK)

            # ---- optional: drop it into the server
            server_phar = next((p for p in (HERE.parent / "server" / "Pocketmine-MP.phar", HERE.parent / "server" / "PocketMine-MP.phar")
                                if p.is_file()), None)
            if interactive and server_phar is not None and server_phar.resolve() != out_path:
                out()
                if confirm(f"Copy to server ({server_phar.name}, backup kept)"):
                    try:
                        bak = backup(server_phar)
                        shutil.copy2(out_path, server_phar)
                        out(f"  {GREEN}{OK}{RESET} Copied. Backup: {GREY}{bak.name if bak else '-'}{RESET}")
                    except PermissionError:
                        out(f"  {RED}{FAIL}{RESET} File in use. Stop the server and retry")
            out(f"\n  {GREY}{DOT} Log {log_path}{RESET}\n")
            return 0

        except BuildError as e:
            log.write(f"\nERROR: {e}\n")
            out()
            box([f"{RED}{BOLD}{FAIL} Failed:{RESET} {e}"] + ([""] + [f"{GREY}{l[:110]}{RESET}" for l in e.tail] if e.tail else [])
                + ["", f"{GREY}{DOT} Log {log_path}{RESET}"], RED)
            out()
            return 1
        except KeyboardInterrupt:
            out(f"\n\n  {PINK}{WARN} Cancelled{RESET}\n")
            return 130
        finally:
            out(SHOW, end="")
            if workdir is not None and workdir.exists() and not args.keep_source:
                try:
                    force_rmtree(workdir)
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
