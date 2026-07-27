#!/usr/bin/env python3
"""
scripts/lint_constraints.py — deterministic, zero-LLM hard-constraint linter.

Enforces the CLAUDE.md HARD CONSTRAINTS mechanically. Operates on a diff range
(default: staged changes; --range A..B for CI) plus whole-tree greps where the
constraint is global. Modeled on scripts/lint_data.py: same Finding shape, same
ERROR/WARN severities, same lint()/print_report()/main(argv) split, same --json,
same sys.exit(main()).

Read-only over source files. It never imports the engine, never writes books.db,
spends no API. It is wired into scripts/hooks/pre-commit (staged diff) BEFORE the
snapshot regeneration, so a hard-constraint violation blocks the commit early.

Checks:
  1 engine-immutable  diff touches predict_engine/db_loader/views/nonfiction_engine  ERROR*
  2 write-path        mutating SQL in a .py outside the sanctioned write path        ERROR
  3 resid-ci-guard    a resid_sd-derived CI on a SERVED surface (the reg. guard)     ERROR
  4 showcase-env      NEXT_PUBLIC_SUPABASE reachable from a STATIC_DATA build path    ERROR
  5 export-coupling   a new backend response key with no export_static_data.py entry  WARN
  6 design-tokens     a NEW hex color literal in frontend/ outside globals.css        WARN
  7 secrets           apikey.txt/apikey.py or an sk-ant-… key in the diff             ERROR

  * ERROR unless the commit-message body carries an `engine-change:` marker (the
    deliberate escape hatch — a sanctioned engine change is possible but never
    accidental). In pre-commit the marker is read from COMMIT_EDITMSG; in
    --range mode from the range's commit logs; the env var
    LINT_CONSTRAINTS_ALLOW_ENGINE=1 overrides in either.

SCOPING NOTES (why the ERROR checks pass clean on HEAD — see also the brief):
  * Check 2 exempts db_write.py, db_backend.py, migrate_*.py, backfill_*.py,
    test_*.py, and everything under scripts/ (mirrors lint_data.py's own posture:
    read-only SELECT is unrestricted; the fixtures in test_*.py legitimately
    DELETE from a scratch DB).
  * Check 3 is NOT "any resid_sd on a served surface" — resid_sd is a legitimate,
    CLAUDE.md-sanctioned calibration DIAGNOSTIC (served as a bare `resid_sd` key /
    shown on the calibration + methodology pages). The ERROR is the reintroduced
    resid_sd-derived *interval*: a `1.645`+`resid_sd` computation, or a `resid_sd`
    paired with 90%/95%-CI language, on backend/main.py or a frontend source file.
    Comments and generated trees (frontend/public, frontend/.next) are ignored.

Usage:
    python3 scripts/lint_constraints.py                 # staged diff (pre-commit)
    python3 scripts/lint_constraints.py --range main..HEAD
    python3 scripts/lint_constraints.py --all           # whole tree, global checks only
    python3 scripts/lint_constraints.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Same 4-field shape as lint_data.Finding; `table` holds the check id.
Finding = namedtuple("Finding", "level table title message")

ENGINE_FILES = {"predict_engine.py", "db_loader.py", "views.py", "nonfiction_engine.py"}
SERVED_PY = "backend/main.py"
EXPORT_SCRIPT = "scripts/export_static_data.py"
GLOBALS_CSS = "frontend/app/globals.css"
SECRET_FILES = {"apikey.txt", "apikey.py"}

# Binary / generated extensions we never scan for secrets or content.
_SKIP_EXT = (".db", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff",
             ".woff2", ".ttf", ".otf", ".pdf", ".xlsx", ".zip", ".lock")

_SQL_MUTATE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE\s+[A-Za-z_.\"'`]+\s+SET|DELETE\s+FROM|ALTER\s+TABLE)\b",
    re.I)
_CI_PHRASE = re.compile(r"9[05]\s*%?\s*CI\b|confidence\s+interval", re.I)
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_SECRET_KEY = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
_NEW_KEY = re.compile(r'["\'][a-zA-Z_][a-zA-Z0-9_]*["\']\s*:')


# ── git plumbing ────────────────────────────────────────────────────────────────
def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True)


def _tracked_files():
    p = _git("ls-files")
    return [l for l in p.stdout.splitlines() if l.strip()]


def _diff_name_only(range_spec):
    args = ["diff", "--name-only", range_spec] if range_spec \
        else ["diff", "--cached", "--name-only"]
    return [l for l in _git(*args).stdout.splitlines() if l.strip()]


def _diff_added(range_spec):
    """Return {file: [(new_lineno, text), …]} for added ('+') lines only."""
    args = ["diff", "-U0", range_spec] if range_spec \
        else ["diff", "--cached", "-U0"]
    added: dict[str, list] = {}
    cur = None
    newno = 0
    for line in _git(*args).stdout.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            newno = int(m.group(1)) if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if cur is not None:
                added.setdefault(cur, []).append((newno, line[1:]))
            newno += 1
        elif not line.startswith("-"):
            newno += 1
    return added


def _git_dir() -> Path:
    p = _git("rev-parse", "--git-dir")
    raw = p.stdout.strip() if p.returncode == 0 else ".git"
    path = Path(raw)
    return path if path.is_absolute() else (ROOT / path)


def engine_change_allowed(range_spec) -> bool:
    """True if the deliberate escape hatch is present."""
    if os.environ.get("LINT_CONSTRAINTS_ALLOW_ENGINE"):
        return True
    if range_spec:
        body = _git("log", "--format=%B", range_spec).stdout
    else:
        try:
            body = (_git_dir() / "COMMIT_EDITMSG").read_text(encoding="utf-8")
        except OSError:
            body = ""
    return "engine-change:" in body


# ── file helpers ────────────────────────────────────────────────────────────────
def _base(path):
    return os.path.basename(path)


def _is_write_exempt(path: str) -> bool:
    base = _base(path)
    if base in ("db_write.py", "db_backend.py"):
        return True
    if base.startswith(("migrate_", "backfill_", "test_")):
        return True
    return path.startswith("scripts/") or "/scripts/" in path


def _is_frontend_src(path: str) -> bool:
    if not path.startswith("frontend/"):
        return False
    if path.startswith(("frontend/public/", "frontend/.next/")):
        return False
    if "node_modules/" in path:
        return False
    return path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs"))


def _is_scannable_text(path: str) -> bool:
    if path.endswith(_SKIP_EXT):
        return False
    if "node_modules/" in path or "/.next/" in path or path.startswith("frontend/.next/"):
        return False
    return True


def _file_text(path) -> str:
    try:
        return (ROOT / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _is_full_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith(("#", "//", "*"))


def _strip_inline_comment(line: str) -> str:
    out = line
    for mk in ("#", "//"):
        i = out.find(mk)
        if i != -1:
            out = out[:i]
    return out


def _reintroduces_resid_ci(line: str) -> bool:
    """The regression signature: a resid_sd-derived served interval (NOT the bare
    resid_sd diagnostic). Comments already stripped by the caller."""
    code = _strip_inline_comment(line)
    has_resid = "resid_sd" in code
    has_1645 = "1.645" in code
    if has_resid and has_1645:
        return True
    if _CI_PHRASE.search(code) and (has_resid or has_1645):
        return True
    return False


# ── checks ──────────────────────────────────────────────────────────────────────
def check_engine_immutability(results, changed_files, range_spec):
    touched = sorted(f for f in changed_files if _base(f) in ENGINE_FILES)
    if touched and not engine_change_allowed(range_spec):
        results["errors"].append(Finding(
            "ERROR", "engine-immutable", ", ".join(touched),
            "read-only engine file(s) modified. If this is a sanctioned change, "
            "add a line 'engine-change: <reason>' to the commit message body "
            "(or set LINT_CONSTRAINTS_ALLOW_ENGINE=1); otherwise revert."))


def _iter_lines(records):
    """records is a list of (lineno, text)."""
    for lineno, text in records:
        yield lineno, text


def check_write_path(results, source):
    """source: dict file -> list[(lineno, text)] (diff) or -> whole-file lines."""
    for path, records in source.items():
        if not path.endswith(".py") or _is_write_exempt(path):
            continue
        for lineno, text in records:
            if _is_full_comment(text):
                continue
            if _SQL_MUTATE.search(text):
                results["errors"].append(Finding(
                    "ERROR", "write-path", f"{path}:{lineno}",
                    f"raw mutating SQL outside the write path: {text.strip()[:80]!r}. "
                    f"All writes must go through db_write.py's validated functions."))


def check_resid_ci(results, source, diff_mode):
    for path, records in source.items():
        if path != SERVED_PY and not _is_frontend_src(path):
            continue
        for lineno, text in records:
            if _is_full_comment(text):
                continue
            if _reintroduces_resid_ci(text):
                results["errors"].append(Finding(
                    "ERROR", "resid-ci-guard", f"{path}:{lineno}",
                    f"a resid_sd-derived interval on a served surface: "
                    f"{text.strip()[:80]!r}. The only served interval is the "
                    f"conformal 80% band (intervals.py) — never ±1.645·resid_sd."))
            elif diff_mode and ("resid_sd" in _strip_inline_comment(text)
                                or "1.645" in _strip_inline_comment(text)):
                results["warns"].append(Finding(
                    "WARN", "resid-ci-guard", f"{path}:{lineno}",
                    "resid_sd/1.645 added to a served surface — confirm it is the "
                    "calibration DIAGNOSTIC, not a served prediction interval."))


def check_supabase_static(results, files):
    for f in files:
        if not _is_frontend_src(f):
            continue
        txt = _file_text(f)
        if "STATIC_DATA" in txt and "NEXT_PUBLIC_SUPABASE" in txt:
            results["errors"].append(Finding(
                "ERROR", "showcase-env", f,
                "file references BOTH STATIC_DATA and NEXT_PUBLIC_SUPABASE — the "
                "static showcase must carry no Supabase vars (it would flip to a "
                "login wall). Keep the Supabase reference out of the static path."))


def check_export_coupling(results, changed_files, added):
    if SERVED_PY not in changed_files:
        return
    if EXPORT_SCRIPT in changed_files:
        return  # the exporter moved too — assume it was handled
    new_keys = []
    for lineno, text in added.get(SERVED_PY, []):
        if _is_full_comment(text):
            continue
        for m in _NEW_KEY.finditer(text):
            new_keys.append(m.group(0).rstrip(":").strip())
    if new_keys:
        sample = ", ".join(sorted(set(new_keys))[:6])
        results["warns"].append(Finding(
            "WARN", "export-coupling", SERVED_PY,
            f"new response key(s) added ({sample}) but {EXPORT_SCRIPT} is "
            f"untouched — a display feature that shows new data must ALSO be added "
            f"to the export, or the static showcase has nothing to render. "
            f"(heuristic — ignore if these aren't served payload keys.)"))


def check_design_tokens(results, added):
    for path, records in added.items():
        if not path.startswith("frontend/") or path == GLOBALS_CSS:
            continue
        if not _is_scannable_text(path):
            continue
        for lineno, text in records:
            if _is_full_comment(text):
                continue
            for m in _HEX.finditer(text):
                results["warns"].append(Finding(
                    "WARN", "design-tokens", f"{path}:{lineno}",
                    f"new hex color literal {m.group(0)} outside globals.css — "
                    f"reuse an existing design token (the 'Fable' system) instead."))


def check_secrets(results, changed_files, source):
    for f in changed_files:
        if _base(f) in SECRET_FILES:
            results["errors"].append(Finding(
                "ERROR", "secrets", f,
                "a secret key file is staged — unstage it (git rm --cached) and "
                "keep it gitignored. Never commit an API key."))
    for path, records in source.items():
        if not _is_scannable_text(path):
            continue
        for lineno, text in records:
            if _SECRET_KEY.search(text):
                results["errors"].append(Finding(
                    "ERROR", "secrets", f"{path}:{lineno}",
                    "an Anthropic API key (sk-ant-…) appears here — remove it "
                    "immediately and rotate the key."))


# ── driver ──────────────────────────────────────────────────────────────────────
def _whole_tree_source(tracked):
    """{file: [(lineno, text), …]} for every scannable tracked file."""
    source = {}
    for f in tracked:
        if not _is_scannable_text(f):
            continue
        lines = _file_text(f).split("\n")
        source[f] = list(enumerate(lines, 1))
    return source


def lint(mode="staged", range_spec=None) -> dict:
    """mode ∈ {'staged','range','all'}. Returns {'errors','warns'} of Findings.
    Read-only; nothing here writes."""
    results = {"errors": [], "warns": []}

    if mode == "all":
        tracked = _tracked_files()
        source = _whole_tree_source(tracked)
        # Global invariants only (checks 2, 3, 4, 7); the diff-scoped checks
        # (1 immutability, 5 export coupling, 6 'new' hex) have no meaning here.
        check_write_path(results, source)
        check_resid_ci(results, source, diff_mode=False)
        check_supabase_static(results, [f for f in tracked if _is_frontend_src(f)])
        check_secrets(results, tracked, source)
        return results

    # diff modes: staged (default) or range
    changed = _diff_name_only(range_spec)
    added = _diff_added(range_spec)
    check_engine_immutability(results, changed, range_spec)
    check_write_path(results, added)
    check_resid_ci(results, added, diff_mode=True)
    check_supabase_static(results, [f for f in changed if _is_frontend_src(f)])
    check_export_coupling(results, changed, added)
    check_design_tokens(results, added)
    check_secrets(results, changed, added)
    return results


def format_lines(result: dict) -> list[str]:
    return [f"{f.level:5} | {f.table} | {f.title} | {f.message}"
            for f in result["errors"] + result["warns"]]


def print_report(result: dict, stream=sys.stdout) -> None:
    for line in format_lines(result):
        print(line, file=stream)
    ne, nw = len(result["errors"]), len(result["warns"])
    print(f"constraint lint: {ne} error(s), {nw} warning(s).", file=stream)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic hard-constraint linter (CLAUDE.md).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--range", metavar="A..B", help="lint a commit range (CI)")
    g.add_argument("--all", action="store_true",
                   help="whole tree, global checks only")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.all:
        result = lint(mode="all")
    elif args.range:
        result = lint(mode="range", range_spec=args.range)
    else:
        result = lint(mode="staged")

    if args.json:
        print(json.dumps({
            "errors": [f._asdict() for f in result["errors"]],
            "warns": [f._asdict() for f in result["warns"]],
            "summary": {"errors": len(result["errors"]),
                        "warnings": len(result["warns"])},
        }, indent=2, ensure_ascii=False))
    else:
        print_report(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
