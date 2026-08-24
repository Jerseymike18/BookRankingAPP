#!/usr/bin/env python3
"""
scripts/lint_docs.py — deterministic, zero-LLM doc-drift auditor.

Turns the falsifiable prose in CLAUDE.md + ARCHITECTURE.md into machine-checked
claims and compares each against ground truth in the repo. Modeled directly on
scripts/lint_data.py: same Finding shape, same ERROR/WARN severities, the same
lint() / print_report() / main(argv) split, the same --json flag, the same
sys.exit(main()) at the bottom.

Read-only over source files + committed artifacts. It never imports the engine,
never writes books.db, spends no API. Claims are located by *anchored regex on
the doc line* (not by line position), so the checks survive the docs being
re-flowed.

Checks (claim → ground truth):
  1 test-count       docs "test_engine.py N/N"           → run test_engine.py               ERROR (numeric)
  2 walkforward-mae  ARCHITECTURE.md honest MAE           → validation/walkforward_report.md ERROR (numeric)
  3 interval-cov     CLAUDE.md served-coverage %          → validation/interval_coverage.md  ERROR (numeric)
  4 pages            doc page lists                       → glob frontend/app/**/page.tsx     ERROR (structural)
  5 tenant-tables    ARCHITECTURE.md "N per-user tables"  → books.db user_id columns         ERROR (structural)
  6 db-write-fns     CLAUDE.md "e.g." function list       → AST public defs of db_write.py   WARN
  7 versions         docs "Next.js NN" / "React NN"       → frontend/package.json            WARN (numeric)

Autonomy split (the important design decision):
  --fix auto-rewrites NUMERIC-only drift (checks 1, 2, 3, 7) — mechanical,
  single-token, independently verifiable, zero judgment. STRUCTURAL drift
  (checks 4, 5) is NEVER auto-fixed: a new undocumented page needs prose. Those
  are reported with the exact edit for a human PR.

Usage:
    python3 scripts/lint_docs.py            # human report, exit 1 iff any ERROR
    python3 scripts/lint_docs.py --json     # machine-readable
    python3 scripts/lint_docs.py --fix      # numeric drift only; prints what it changed
    python3 scripts/lint_docs.py --no-run-tests   # skip the slow test_engine.py run
                                                  # (check 1 reports SKIPPED, not PASS)

Wired into scripts/hooks/pre-push (with --no-run-tests, for speed). The full
check — including running test_engine.py — is a manual / CI run.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = {
    "CLAUDE.md": ROOT / "CLAUDE.md",
    "ARCHITECTURE.md": ROOT / "ARCHITECTURE.md",
}
DEFAULT_DB = ROOT / "books.db"
WALKFORWARD_REPORT = ROOT / "validation" / "walkforward_report.md"
INTERVAL_COVERAGE = ROOT / "validation" / "interval_coverage.md"
DB_WRITE = ROOT / "db_write.py"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
FRONTEND_APP = ROOT / "frontend" / "app"

# Same 4-field shape as lint_data.Finding, so the JSON serialization and the
# report formatter carry over unchanged. Here `table` holds the check id.
Finding = namedtuple("Finding", "level table title message")
# A single mechanical rewrite that --fix can apply. Only numeric checks emit these.
Fix = namedtuple("Fix", "path lineno old new label")


# ── doc reading (tiny files; read once, cached) ─────────────────────────────────
_DOC_CACHE: dict[str, str] = {}


def _doc_text(name: str) -> str:
    if name not in _DOC_CACHE:
        _DOC_CACHE[name] = DOCS[name].read_text(encoding="utf-8")
    return _DOC_CACHE[name]


def _doc_lines(name: str) -> list[str]:
    return _doc_text(name).split("\n")


def _rel(path) -> str:
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return str(path)


def _section(text: str, header: str) -> str:
    """Return the body of the markdown section whose header CONTAINS `header`
    (case-insensitive), up to the next header of any level. Empty if not found."""
    out: list[str] = []
    grabbing = False
    for line in text.splitlines():
        if re.match(r"^#{1,6}\s", line):
            if grabbing:
                break
            grabbing = header.lower() in line.lower()
            continue
        if grabbing:
            out.append(line)
    return "\n".join(out)


# ── ground-truth readers ────────────────────────────────────────────────────────
def ground_truth_test_count(timeout: int = 600):
    """Run test_engine.py and return (total_checks, n_fail). total is taken from
    the 'ALL N CHECKS PASSED' banner when present, else counted from [PASS]/[FAIL]
    markers. Returns (None, None) if it cannot be determined."""
    try:
        proc = subprocess.run(
            [sys.executable, "test_engine.py"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        print(f"WARN: could not run test_engine.py: {exc}", file=sys.stderr)
        return None, None
    out = proc.stdout + proc.stderr
    n_fail = len(re.findall(r"\[FAIL\]", out))
    m = re.search(r"ALL\s+(\d+)\s+CHECKS\s+PASSED", out)
    if m:
        return int(m.group(1)), n_fail
    n_pass = len(re.findall(r"\[PASS\]", out))
    if n_pass or n_fail:
        return n_pass + n_fail, n_fail
    return None, None


def ground_truth_honest_mae():
    """The honest-variant WA MAE from the 'Overall WA MAE' table of the
    walk-forward report (the pre-refine, memory-only baseline row)."""
    if not WALKFORWARD_REPORT.exists():
        return None
    section = _section(WALKFORWARD_REPORT.read_text(encoding="utf-8"), "Overall WA MAE")
    for line in section.splitlines():
        if line.lstrip().startswith("|") and "honest" in line.lower():
            m = re.search(r"([0-9]+\.[0-9]+)", line)
            if m:
                return float(m.group(1))
    return None


def ground_truth_interval_coverage():
    """The measured coverage % of the served conformal band from
    interval_coverage.md (the bolded 'measured coverage' cell)."""
    if not INTERVAL_COVERAGE.exists():
        return None
    for line in INTERVAL_COVERAGE.read_text(encoding="utf-8").splitlines():
        if "served conformal" in line.lower():
            m = re.search(r"\*\*([0-9]+\.[0-9]+)%\*\*", line)
            if m:
                return float(m.group(1))
            nums = re.findall(r"([0-9]+\.[0-9]+)%", line)
            if nums:
                return float(nums[-1])
    return None


def _sqlite_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)


def ground_truth_tenant_tables(db_path: Path):
    """Return (all_tables, tables_with_user_id) as sets. Read-only."""
    con = _sqlite_ro(db_path)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        with_uid = set()
        for t in tables:
            cols = {c[1] for c in con.execute(f'PRAGMA table_info("{t}")')}
            if "user_id" in cols:
                with_uid.add(t)
        return tables, with_uid
    finally:
        con.close()


def ground_truth_db_write_defs():
    """Public (non-underscore) top-level function names defined in db_write.py."""
    tree = ast.parse(DB_WRITE.read_text(encoding="utf-8"))
    return {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    }


def ground_truth_frontend_routes():
    """Route paths from every frontend/app/**/page.tsx. Home is '/'. Next.js
    route-group '(group)' and dynamic '[param]' segments are dropped from the
    path (they don't appear in the docs' page list)."""
    routes = set()
    for p in FRONTEND_APP.rglob("page.tsx"):
        rel = p.parent.relative_to(FRONTEND_APP)
        parts = [seg for seg in rel.parts
                 if not (seg.startswith("(") and seg.endswith(")"))
                 and not (seg.startswith("[") and seg.endswith("]"))]
        routes.add("/".join(parts) if parts else "/")
    return routes


def documented_routes():
    """Union of the page routes enumerated in both docs' page lists, plus the
    'top-level X (fiction)' → 'fiction/X' aliases the docs annotate.

    Returns (documented, aliases) where `aliases` maps a group route like
    'fiction/read-queue' to the top-level token ('read-queue') that realizes it —
    the fiction read-queue physically lives at the top-level /read-queue route, so
    the doc-group form is not a missing page.
    """
    documented: set[str] = set()
    aliases: dict[str, str] = {}
    for name in DOCS:
        if name == "CLAUDE.md":
            region = _section(_doc_text(name), "Pages (frontend/app/)")
        else:  # ARCHITECTURE.md — the "`app/*` — pages" bullet
            region = ""
            for line in _doc_lines(name):
                if "`app/*`" in line and "page" in line.lower():
                    region = line
                    break
        if not region:
            continue

        # Top-level pages: fully-backticked lowercase route tokens, plus the
        # literal word 'home' → '/'.
        for m in re.finditer(r"`([A-Za-z0-9/_-]+)`", region):
            tok = m.group(1)
            if tok == "/":
                documented.add("/")
            elif re.fullmatch(r"[a-z][a-z0-9-]*", tok):
                documented.add(tok)
            # Nested route outside the fiction/nonfiction groups handled below —
            # e.g. `predict/genres`, the Genre Prediction page under the Predict
            # nav group. Those groups get their own brace/paren forms because the
            # docs write them as a set; a one-off nested page is just written out.
            elif re.fullmatch(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)+", tok):
                documented.add(tok)
        if re.search(r"\bhome\b", region):
            documented.add("/")

        # Group brace form:  fiction/{rankings, tier-list, …}
        for m in re.finditer(r"(fiction|nonfiction)/\{([^}]+)\}", region):
            group = m.group(1)
            for mem in m.group(2).split(","):
                mem = mem.strip().strip("`")
                if mem:
                    documented.add(f"{group}/{mem}")

        # Group paren form:  fiction/* + nonfiction/* (rankings, …, read-queue)
        for m in re.finditer(
            r"fiction/\*.*?nonfiction/\*\s*\(([^)]+)\)", region, re.S
        ):
            members = [x.strip().strip("`") for x in m.group(1).split(",")]
            for group in ("fiction", "nonfiction"):
                for mem in members:
                    if mem:
                        documented.add(f"{group}/{mem}")

        # Annotation:  `read-queue` (fiction)  → top-level read-queue realizes
        # fiction/read-queue.
        for m in re.finditer(r"`([a-z0-9-]+)`\s*\((fiction|nonfiction)\)", region):
            tok, group = m.group(1), m.group(2)
            aliases[f"{group}/{tok}"] = tok

    return documented, aliases


# ── checks ──────────────────────────────────────────────────────────────────────
# A line asserting the test gate must carry one of these context words next to
# the N/N — keeps the check off unrelated equal-number pairs.
_TEST_ANCHORS = ("test_engine", "must stay", "currently", "health gate", "checks")


def check_test_count(results: dict, run_tests: bool) -> None:
    claims = []  # (doc, lineno, claimed_int, matched_token)
    for name in DOCS:
        for i, line in enumerate(_doc_lines(name), 1):
            low = line.lower()
            if not any(a in low for a in _TEST_ANCHORS):
                continue
            for m in re.finditer(r"\b(\d+)/(\d+)\b", line):
                if m.group(1) == m.group(2):
                    claims.append((name, i, int(m.group(1)), m.group(0)))
    if not claims:
        return

    if not run_tests:
        locs = ", ".join(f"{d}:{ln}" for d, ln, _, _ in claims)
        results["skipped"].append(Finding(
            "SKIP", "test-count", locs,
            "test_engine.py not run (--no-run-tests); test-gate count unverified"))
        return

    total, n_fail = ground_truth_test_count()
    if total is None:
        results["warns"].append(Finding(
            "WARN", "test-count", "test_engine.py",
            "could not determine the test_engine.py check count"))
        return

    drifted = [c for c in claims if c[2] != total]
    if drifted:
        locs = ", ".join(f"{d}:{ln}" for d, ln, _, _ in drifted)
        note = f" ({n_fail} check(s) currently FAILING)" if n_fail else ""
        results["errors"].append(Finding(
            "ERROR", "test-count", locs,
            f"docs claim {drifted[0][2]}/{drifted[0][2]} but test_engine.py has "
            f"{total}/{total} checks{note}; fix → {total}/{total}"))
        for d, ln, _, tok in drifted:
            results["fixes"].append(Fix(DOCS[d], ln, tok, f"{total}/{total}", "test-count"))


def check_walkforward_mae(results: dict) -> None:
    gt = ground_truth_honest_mae()
    claims = []  # (doc, lineno, token)
    for name in DOCS:
        for i, line in enumerate(_doc_lines(name), 1):
            low = line.lower()
            if "mae" not in low:
                continue
            if not ("walk-forward" in low or "walkforward" in low or "baseline" in low):
                continue
            m = re.search(r"([0-9]\.[0-9]{2,})", line)
            if m:
                claims.append((name, i, m.group(1)))
    if not claims:
        return
    if gt is None:
        locs = ", ".join(f"{d}:{ln}" for d, ln, _ in claims)
        results["warns"].append(Finding(
            "WARN", "walkforward-mae", locs,
            "cannot read the honest MAE from validation/walkforward_report.md"))
        return
    for name, ln, tok in claims:
        dec = len(tok.split(".")[1])
        if round(float(tok), dec) != round(gt, dec):
            new = f"{gt:.{dec}f}"
            results["errors"].append(Finding(
                "ERROR", "walkforward-mae", f"{name}:{ln}",
                f"doc claims honest walk-forward MAE {tok} but the report's honest "
                f"row is {new}; fix → {new}"))
            results["fixes"].append(Fix(DOCS[name], ln, tok, new, "walkforward-mae"))


def check_interval_coverage(results: dict) -> None:
    gt = ground_truth_interval_coverage()
    claims = []  # (doc, lineno, token)
    for name in DOCS:
        for i, line in enumerate(_doc_lines(name), 1):
            low = line.lower()
            if "coverage" not in low:
                continue
            # Only the MEASURED coverage claim (cites the file / honest errors) —
            # NOT the "80% by choice" target level.
            if not ("interval_coverage" in low or "honest" in low):
                continue
            m = re.search(r"([0-9]+\.[0-9]+)%", line)
            if m:
                claims.append((name, i, m.group(1)))
    if not claims:
        return
    if gt is None:
        locs = ", ".join(f"{d}:{ln}" for d, ln, _ in claims)
        results["warns"].append(Finding(
            "WARN", "interval-cov", locs,
            "cannot read served coverage from validation/interval_coverage.md"))
        return
    for name, ln, tok in claims:
        dec = len(tok.split(".")[1])
        if round(float(tok), dec) != round(gt, dec):
            new = f"{gt:.{dec}f}"
            results["errors"].append(Finding(
                "ERROR", "interval-cov", f"{name}:{ln}",
                f"doc claims {tok}% interval coverage but interval_coverage.md "
                f"reports {new}%; fix → {new}"))
            results["fixes"].append(Fix(DOCS[name], ln, f"{tok}%", f"{new}%", "interval-cov"))


def check_pages(results: dict) -> None:
    on_disk = ground_truth_frontend_routes()
    documented, aliases = documented_routes()
    if not documented:
        results["warns"].append(Finding(
            "WARN", "pages", "CLAUDE.md/ARCHITECTURE.md",
            "could not parse a page list from either doc"))
        return
    # A group route is 'satisfied' if it (or its top-level alias) exists on disk.
    satisfied = set(on_disk)
    for group_route, top_tok in aliases.items():
        if top_tok in on_disk:
            satisfied.add(group_route)

    undocumented = sorted(on_disk - documented)
    missing = sorted(documented - satisfied)

    if undocumented:
        results["errors"].append(Finding(
            "ERROR", "pages", "CLAUDE.md/ARCHITECTURE.md",
            f"page(s) exist on disk but appear in NEITHER doc's page list: "
            f"{', '.join(undocumented)}. Add them to the Pages list (needs prose "
            f"— not auto-fixed)."))
    if missing:
        results["errors"].append(Finding(
            "ERROR", "pages", "CLAUDE.md/ARCHITECTURE.md",
            f"page(s) documented but no frontend/app/**/page.tsx exists: "
            f"{', '.join(missing)}. Build the page or remove it from the docs "
            f"(needs a human decision — not auto-fixed)."))


def check_tenant_tables(results: dict, db_path: Path) -> None:
    # Parse the explicit named list ("N per-user tables: `a`, `b`, …").
    named: list[str] = []
    stated = None
    lineno = None
    for i, line in enumerate(_doc_lines("ARCHITECTURE.md"), 1):
        m = re.search(r"(\d+)\s+per-user tables?", line, re.I)
        if m and "`" in line:
            stated = int(m.group(1))
            # Fully-backticked snake_case identifiers only (skips e.g. `books.db`).
            named = re.findall(r"`([a-z][a-z_]*)`", line)
            lineno = i
            break
    if not named:
        return

    all_tables, with_uid = ground_truth_tenant_tables(db_path)
    absent = [t for t in named if t not in all_tables]
    no_uid = [t for t in named if t in all_tables and t not in with_uid]
    loc = f"ARCHITECTURE.md:{lineno}"

    if stated is not None and stated != len(named):
        results["warns"].append(Finding(
            "WARN", "tenant-tables", loc,
            f"says {stated} per-user tables but lists {len(named)} names"))

    problems = []
    if absent:
        problems.append(f"named table(s) not in the schema: {', '.join(absent)}")
    if no_uid:
        problems.append(f"named table(s) lack a user_id column: {', '.join(no_uid)}")
    if problems:
        results["errors"].append(Finding(
            "ERROR", "tenant-tables", loc,
            "; ".join(problems) + " — reconcile the doc list with the schema "
            "(structural — not auto-fixed)."))


def check_db_write_fns(results: dict) -> None:
    text = _doc_text("CLAUDE.md")
    m = re.search(r"e\.g\.(.*?nonfiction equivalents)", text, re.S)
    if not m:
        return
    listed = re.findall(r"`([a-z][a-z_]*)`", m.group(1))
    public = ground_truth_db_write_defs()
    missing = [n for n in listed if n not in public]
    if missing:
        results["warns"].append(Finding(
            "WARN", "db-write-fns", "CLAUDE.md",
            f"'e.g.'-listed db_write function(s) with no matching public def: "
            f"{', '.join(missing)} (list is illustrative — verify it was not "
            f"renamed)"))


def check_versions(results: dict) -> None:
    if not PACKAGE_JSON.exists():
        return
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    def major(spec):
        if not spec:
            return None
        m = re.search(r"(\d+)", spec)
        return int(m.group(1)) if m else None

    wanted = {"Next": major(deps.get("next")), "React": major(deps.get("react"))}
    for name in DOCS:
        for i, line in enumerate(_doc_lines(name), 1):
            for m in re.finditer(r"\b(Next(?:\.js)?|React)\s+(\d+)\b", line):
                label = "Next" if m.group(1).lower().startswith("next") else "React"
                gt = wanted.get(label)
                claimed = int(m.group(2))
                if gt is not None and claimed != gt:
                    new = m.group(0).replace(m.group(2), str(gt))
                    results["warns"].append(Finding(
                        "WARN", "versions", f"{name}:{i}",
                        f"doc says {label} {claimed} but package.json is "
                        f"{label} {gt}; fix → {new}"))
                    results["fixes"].append(
                        Fix(DOCS[name], i, m.group(0), new, "versions"))


# ── driver ──────────────────────────────────────────────────────────────────────
def lint(run_tests: bool = True, db_path: Path = DEFAULT_DB) -> dict:
    """Run every check. Returns {'errors','warns','skipped','fixes'} — the first
    three are Finding lists, 'fixes' are the mechanical rewrites --fix can apply.
    Read-only; nothing here writes."""
    results: dict = {"errors": [], "warns": [], "skipped": [], "fixes": []}
    check_test_count(results, run_tests)
    check_walkforward_mae(results)
    check_interval_coverage(results)
    check_pages(results)
    check_tenant_tables(results, db_path)
    check_db_write_fns(results)
    check_versions(results)
    return results


def format_lines(result: dict) -> list[str]:
    lines = []
    for f in result["errors"] + result["warns"] + result["skipped"]:
        lines.append(f"{f.level:5} | {f.table} | {f.title} | {f.message}")
    return lines


def print_report(result: dict, stream=sys.stdout) -> None:
    for line in format_lines(result):
        print(line, file=stream)
    ne, nw, ns = len(result["errors"]), len(result["warns"]), len(result["skipped"])
    print(f"doc lint: {ne} error(s), {nw} warning(s), {ns} skipped.", file=stream)


def apply_fixes(fixes, stream=sys.stdout) -> int:
    """Apply the numeric rewrites in-place, line-scoped and single-occurrence.
    Prints every change. A pattern that no longer matches (already fixed) is
    reported and skipped, never forced."""
    by_path: dict[Path, list] = {}
    for fx in fixes:
        by_path.setdefault(fx.path, []).append(fx)
    changed = 0
    for path, fxs in by_path.items():
        lines = Path(path).read_text(encoding="utf-8").split("\n")
        for fx in fxs:
            idx = fx.lineno - 1
            if 0 <= idx < len(lines) and fx.old in lines[idx]:
                lines[idx] = lines[idx].replace(fx.old, fx.new, 1)
                changed += 1
                print(f"  fixed {_rel(path)}:{fx.lineno} [{fx.label}] "
                      f"{fx.old!r} → {fx.new!r}", file=stream)
            else:
                print(f"  skip  {_rel(path)}:{fx.lineno} [{fx.label}] "
                      f"pattern {fx.old!r} not found (already fixed?)", file=stream)
        Path(path).write_text("\n".join(lines), encoding="utf-8")
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic doc-drift auditor for CLAUDE.md + ARCHITECTURE.md.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fix", action="store_true",
                    help="auto-rewrite NUMERIC drift only (checks 1,2,3,7)")
    ap.add_argument("--no-run-tests", action="store_true",
                    help="skip running test_engine.py (check 1 → SKIPPED)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="path to the SQLite DB")
    args = ap.parse_args(argv)

    result = lint(run_tests=not args.no_run_tests, db_path=Path(args.db))

    if args.fix:
        n = apply_fixes(result["fixes"])
        structural = [f for f in result["errors"] if not f.table.startswith(
            ("test-count", "walkforward-mae", "interval-cov"))]
        print(f"applied {n} numeric fix(es). "
              f"{len(structural)} structural finding(s) remain for a human PR "
              f"(re-run to confirm).")
        return 0

    if args.json:
        print(json.dumps({
            "errors": [f._asdict() for f in result["errors"]],
            "warns": [f._asdict() for f in result["warns"]],
            "skipped": [f._asdict() for f in result["skipped"]],
            "fixes": [{"path": _rel(fx.path), "lineno": fx.lineno,
                       "old": fx.old, "new": fx.new, "label": fx.label}
                      for fx in result["fixes"]],
            "summary": {"errors": len(result["errors"]),
                        "warnings": len(result["warns"]),
                        "skipped": len(result["skipped"])},
        }, indent=2, ensure_ascii=False))
    else:
        print_report(result)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
