#!/usr/bin/env python3
"""
repomap v3 — paste a repo URL, get a first-pass onboarding friction report.
Supports optional LLM-powered entry point summarization via Anthropic, OpenAI,
Ollama, or any OpenAI-compatible endpoint.

New in v3: deep analysis passes via analyzers.py
  - first_day_path          ordered new-dev checklist
  - naming_consistency      style clash detection across files/dirs/symbols
  - entry_point_confidence  scored entry points with rationale
  - flow_trace              import/call chain graph from top entry points
  - parameter_tracking      env vars, config keys, CLI flags inventory
  - route_detection         rich route extraction (method, path, framework)
  - dependency_impact       dep cross-reference: how many files use each one
  - hidden_complexity       concurrency, god files, dynamic dispatch, secrets…
  - architecture            architectural pattern classification
"""

import sys
import json
import shutil
import subprocess
import tempfile
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from typing import Optional

import click

# Deep analysis, symbol graph, dashboard, and LLM layers are all optional —
# gracefully skipped if their files aren't present alongside repomap.py.
try:
    import analyzers as _analyzers
    ANALYZERS_AVAILABLE = True
except ImportError:
    ANALYZERS_AVAILABLE = False

try:
    import symbol_graph as _sg
    SG_AVAILABLE = True
except ImportError:
    SG_AVAILABLE = False

try:
    import report_server as _rs
    RS_AVAILABLE = True
except ImportError:
    RS_AVAILABLE = False

try:
    import llm as _llm
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

# ──────────────────────────────────────────────
# ANSI colour helpers (no rich dep needed)
# ──────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
BLUE   = "\033[34m"
MAGENTA= "\033[35m"

def c(text, *codes): return "".join(codes) + str(text) + RESET
def header(text):    click.echo(c(f"\n{'─'*60}", DIM))
def section(title):  click.echo(c(f"\n  ◆ {title}", BOLD, CYAN))
def item(label, val, color=RESET): click.echo(f"    {c(label+':', DIM)}  {c(val, color)}")
def bullet(text, color=RESET):     click.echo(f"    {c('•', DIM)} {c(text, color)}")
def warn(text):  click.echo(f"    {c('⚠', YELLOW)}  {c(text, YELLOW)}")
def good(text):  click.echo(f"    {c('✓', GREEN)}  {c(text, GREEN)}")
def bad(text):   click.echo(f"    {c('✗', RED)}  {c(text, RED)}")

# ──────────────────────────────────────────────
# FILE-PATTERN DICTIONARIES
# ──────────────────────────────────────────────
ENTRY_POINT_PATTERNS = [
    "main.py", "app.py", "server.py", "run.py", "index.py",
    "manage.py", "wsgi.py", "asgi.py", "cli.py", "start.py",
    "__main__.py", "main.go", "main.ts", "main.js", "index.js",
    "index.ts", "server.js", "server.ts", "app.js", "app.ts",
    "Makefile", "Procfile", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml",
]

ROUTE_PATTERNS = [
    r"routes?/", r"views?/", r"controllers?/", r"handlers?/",
    r"endpoints?/", r"api/",
    r"@app\.(get|post|put|delete|patch)",
    r"@router\.(get|post|put|delete|patch)",
    r"router\.(get|post|put|delete|patch)\(",
    r"path\(", r"url\(", r"Route\(",
]

CONFIG_PATTERNS = [
    ".env", ".env.example", ".env.sample", ".env.template",
    "config.py", "config.js", "config.ts", "config.json", "config.yaml",
    "config.yml", "settings.py", "settings.json", "settings.yaml",
    "settings.yml", "pyproject.toml", "setup.cfg", "setup.py",
    "package.json", "tsconfig.json", "webpack.config.js",
    "vite.config.ts", "vite.config.js", ".babelrc", ".eslintrc",
    ".eslintrc.js", ".eslintrc.json", "jest.config.js", "jest.config.ts",
    "pytest.ini", "tox.ini", "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "requirements.txt", "requirements*.txt",
    "Pipfile", "poetry.lock", "yarn.lock", "package-lock.json",
]

MODEL_PATTERNS = [
    r"models?/", r"schema(s)?/", r"entities/", r"domain/",
    r"db/", r"database/", r"migrations?/",
    r"class\s+\w+\(.*Model",
    r"class\s+\w+\(.*Base",
    r"@dataclass",
    r"mongoose\.model\(",
    r"Schema\(",
    r"sqlalchemy",
    r"prisma",
]

TEST_PATTERNS = [
    r"tests?/", r"__tests__/", r"spec(s)?/",
    r"test_.*\.py$", r".*_test\.py$",
    r".*\.test\.(js|ts)$", r".*\.spec\.(js|ts)$",
    r".*_spec\.rb$",
]

DOC_FILES = [
    "README.md", "README.rst", "README.txt", "README",
    "CONTRIBUTING.md", "CHANGELOG.md", "CHANGELOG", "HISTORY.md",
    "ARCHITECTURE.md", "DESIGN.md", "DEVELOPMENT.md",
    "docs/", "doc/", "wiki/",
]

FRICTION_CHECKS = {
    "no_readme":           ("No README found — new devs have no starting point.", "high"),
    "no_contributing":     ("No CONTRIBUTING guide — contribution process unclear.", "medium"),
    "no_env_example":      ("No .env.example — unclear what env vars are required.", "high"),
    "no_tests":            ("No test directory found — test coverage unknown.", "medium"),
    "no_config":           ("No config file detected — project setup may be manual.", "medium"),
    "no_entry_point":      ("No obvious entry point found — unclear how to run the project.", "high"),
    "no_makefile_nor_scripts": ("No Makefile, scripts/, or package.json scripts — unclear how to build/run.", "low"),
    "large_repo":          ("Repo has 500+ files — onboarding surface area is large.", "low"),
    "many_languages":      ("3+ languages detected — polyglot repo, steeper learning curve.", "low"),
    "has_docker":          ("Docker present — good, environment setup is reproducible.", "positive"),
    "has_ci":              ("CI config found — automated testing pipeline detected.", "positive"),
    "has_lockfile":        ("Dependency lockfile present — deterministic installs.", "positive"),
}

CI_PATTERNS = [
    ".github/workflows", ".circleci", ".travis.yml", "Jenkinsfile",
    ".gitlab-ci.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml",
    ".drone.yml", "circle.yml",
]

LANGUAGE_EXT_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".go": "Go", ".rb": "Ruby", ".java": "Java", ".kt": "Kotlin",
    ".rs": "Rust", ".cs": "C#", ".cpp": "C++", ".c": "C",
    ".php": "PHP", ".swift": "Swift", ".scala": "Scala",
    ".ex": "Elixir", ".exs": "Elixir", ".clj": "Clojure",
    ".hs": "Haskell", ".ml": "OCaml", ".sh": "Shell",
    ".vue": "Vue", ".jsx": "React/JSX", ".tsx": "React/TSX",
}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".env", "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".tox", "eggs", ".eggs", "*.egg-info",
}

# ──────────────────────────────────────────────
# CORE SCANNER
# ──────────────────────────────────────────────
class RepoScanner:
    def __init__(self, repo_path: Path, repo_url: str):
        self.repo_path = repo_path
        self.repo_url  = repo_url
        self.all_files: list[Path] = []
        self.all_dirs:  list[Path] = []
        self.results = {}

    def _walk(self):
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
            rp = Path(root)
            for d in dirs:
                self.all_dirs.append(rp / d)
            for f in files:
                self.all_files.append(rp / f)

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.repo_path))

    def _matches_any(self, path_str: str, patterns: list[str]) -> bool:
        for pat in patterns:
            if re.search(pat, path_str, re.IGNORECASE):
                return True
        return False

    def _find_entry_points(self) -> list[str]:
        found = []
        for f in self.all_files:
            name = f.name
            rel  = self._rel(f)
            if name in ENTRY_POINT_PATTERNS:
                found.append(rel)
            elif name in {"Makefile", "Dockerfile", "Procfile"}:
                found.append(rel)
        return sorted(set(found))

    def _find_routes(self) -> list[str]:
        found = []
        # directory-level matches
        for d in self.all_dirs:
            rel = self._rel(d)
            if self._matches_any(rel, [r"^routes?$", r"/routes?$", r"^views?$", r"/views?$",
                                       r"^controllers?$", r"/controllers?$", r"^api$", r"/api$"]):
                found.append(rel + "/")
        # file-level: scan source files for decorators
        route_files = set()
        for f in self.all_files:
            if f.suffix in (".py", ".js", ".ts", ".go", ".rb") and f.stat().st_size < 500_000:
                try:
                    text = f.read_text(errors="ignore")
                    if self._matches_any(text, ROUTE_PATTERNS[:5]):  # decorator patterns
                        route_files.add(self._rel(f))
                except Exception:
                    pass
        return sorted(set(found) | route_files)

    def _find_configs(self) -> list[str]:
        found = []
        for f in self.all_files:
            name = f.name
            rel  = self._rel(f)
            for pat in CONFIG_PATTERNS:
                if "*" in pat:
                    stem = pat.replace("*", "")
                    if stem in name:
                        found.append(rel)
                        break
                elif name == pat or rel == pat:
                    found.append(rel)
                    break
        return sorted(set(found))

    def _find_models(self) -> list[str]:
        found = []
        for d in self.all_dirs:
            rel = self._rel(d)
            if self._matches_any(rel, [r"(^|/)models?$", r"(^|/)schema(s)?$",
                                       r"(^|/)entities$", r"(^|/)migrations?$"]):
                found.append(rel + "/")
        for f in self.all_files:
            if f.suffix in (".py", ".js", ".ts") and f.stat().st_size < 300_000:
                try:
                    text = f.read_text(errors="ignore")
                    if self._matches_any(text, MODEL_PATTERNS[4:]):
                        found.append(self._rel(f))
                except Exception:
                    pass
        return sorted(set(found))[:20]  # cap for readability

    def _find_tests(self) -> list[str]:
        found = []
        for d in self.all_dirs:
            rel = self._rel(d)
            if self._matches_any(rel, [r"(^|/)tests?$", r"(^|/)__tests__$", r"(^|/)specs?$"]):
                found.append(rel + "/")
        for f in self.all_files:
            rel = self._rel(f)
            if self._matches_any(rel, TEST_PATTERNS[3:]):
                found.append(rel)
        return sorted(set(found))[:20]

    def read_entry_point_contents(self, max_files: int = 5,
                                   max_bytes: int = 4000) -> dict[str, str]:
        """
        Return source content of the most meaningful entry point files.
        Skips binary files and caps each at max_bytes.
        Priority: source files (.py/.js/.ts/.go) over Dockerfile/Makefile.
        """
        candidates = self.results.get("entry_points", [])
        # Sort: source files first
        def priority(p: str) -> int:
            if p.endswith((".py", ".js", ".ts", ".go", ".rb")):
                return 0
            if "Dockerfile" in p or "docker-compose" in p:
                return 2
            return 1

        ranked = sorted(candidates, key=priority)
        out = {}
        for rel in ranked[:max_files]:
            full = self.repo_path / rel
            if not full.exists() or not full.is_file():
                continue
            if full.stat().st_size > 200_000:
                continue
            try:
                text = full.read_text(errors="ignore")
                out[rel] = text[:max_bytes]
            except Exception:
                pass
        return out

    def _find_docs(self) -> list[str]:
        found = []
        for f in self.all_files:
            if f.name.upper().startswith("README") or f.name in DOC_FILES:
                found.append(self._rel(f))
        for d in self.all_dirs:
            if d.name.lower() in ("docs", "doc", "wiki"):
                found.append(self._rel(d) + "/")
        return sorted(set(found))

    def _detect_languages(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for f in self.all_files:
            lang = LANGUAGE_EXT_MAP.get(f.suffix)
            if lang:
                counts[lang] += 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def _detect_ci(self) -> list[str]:
        found = []
        for pat in CI_PATTERNS:
            for d in self.all_dirs:
                if pat in self._rel(d):
                    found.append(pat)
                    break
            for f in self.all_files:
                if pat in self._rel(f):
                    found.append(pat)
                    break
        return sorted(set(found))

    def _has_lockfile(self) -> bool:
        lockfiles = {"package-lock.json", "yarn.lock", "poetry.lock",
                     "Pipfile.lock", "Cargo.lock", "go.sum", "Gemfile.lock"}
        return any(f.name in lockfiles for f in self.all_files)

    def _friction_analysis(self, data: dict) -> list[dict]:
        issues = []

        def add(key):
            msg, severity = FRICTION_CHECKS[key]
            issues.append({"key": key, "message": msg, "severity": severity})

        # negatives
        if not data["docs"]:
            add("no_readme")
        if not any("CONTRIBUTING" in d.upper() for d in data["docs"]):
            add("no_contributing")
        if not any(".env" in c for c in data["configs"]):
            add("no_env_example")
        if not data["tests"]:
            add("no_tests")
        if not data["configs"]:
            add("no_config")
        if not data["entry_points"]:
            add("no_entry_point")

        has_makefile = any("Makefile" in e for e in data["entry_points"])
        has_scripts  = any(f.name == "package.json" for f in self.all_files)
        has_scripts_dir = any("scripts" in self._rel(d).lower() for d in self.all_dirs)
        if not (has_makefile or has_scripts or has_scripts_dir):
            add("no_makefile_nor_scripts")

        if data["file_count"] > 500:
            add("large_repo")
        if len(data["languages"]) >= 3:
            add("many_languages")

        # positives
        has_docker = any("docker" in e.lower() for e in data["entry_points"] + data["configs"])
        if has_docker:
            add("has_docker")
        if data["ci"]:
            add("has_ci")
        if data["has_lockfile"]:
            add("has_lockfile")

        return issues

    def scan(self) -> dict:
        self._walk()
        entry_points = self._find_entry_points()
        routes       = self._find_routes()
        configs      = self._find_configs()
        models       = self._find_models()
        tests        = self._find_tests()
        docs         = self._find_docs()
        languages    = self._detect_languages()
        ci           = self._detect_ci()
        has_lockfile = self._has_lockfile()

        data = {
            "repo_url":     self.repo_url,
            "scanned_at":   datetime.now(timezone.utc).isoformat(),
            "file_count":   len(self.all_files),
            "dir_count":    len(self.all_dirs),
            "languages":    languages,
            "entry_points": entry_points,
            "routes":       routes,
            "configs":      configs,
            "models":       models,
            "tests":        tests,
            "docs":         docs,
            "ci":           ci,
            "has_lockfile": has_lockfile,
            "llm_provider":          None,  # populated later if --llm is used
            "llm_model":             None,
            "llm_entry_summary":     None,
            "llm_onboarding_report": None,
            "analysis": {},                 # populated by analyzers.run_all() after scan
        }
        data["friction"] = self._friction_analysis(data)
        data["score"]    = self._compute_score(data["friction"])
        self.results = data
        return data

    def _compute_score(self, friction: list[dict]) -> dict:
        """Score 0-100. Start at 100, deduct for issues, add back for positives."""
        score = 100
        deductions = {"high": 20, "medium": 10, "low": 5}
        for item in friction:
            if item["severity"] == "positive":
                score += 5
            else:
                score -= deductions.get(item["severity"], 0)
        score = max(0, min(100, score))

        if score >= 75:   label = "Good"
        elif score >= 50: label = "Fair"
        elif score >= 25: label = "Needs work"
        else:             label = "Poor"

        return {"value": score, "label": label}


# ──────────────────────────────────────────────
# OUTPUT FORMATTERS
# ──────────────────────────────────────────────
def _wrap(text: str, width: int) -> list[str]:
    """Simple word-wrap that preserves blank lines as paragraph breaks."""
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            out.append("")
            continue
        words = para.split()
        line  = []
        length = 0
        for w in words:
            if length + len(w) + (1 if line else 0) > width:
                out.append(" ".join(line))
                line, length = [w], len(w)
            else:
                line.append(w)
                length += len(w) + (1 if len(line) > 1 else 0)
        if line:
            out.append(" ".join(line))
    return out


def print_terminal_report(data: dict):
    score = data["score"]
    score_color = GREEN if score["value"] >= 75 else YELLOW if score["value"] >= 50 else RED

    click.echo()
    click.echo(c("  ┌─────────────────────────────────────────────────────┐", DIM))
    click.echo(c("  │", DIM) + c("  repomap", BOLD, CYAN) + c(" — onboarding friction report", DIM) + c("               │", DIM))
    click.echo(c("  └─────────────────────────────────────────────────────┘", DIM))

    section("Repository")
    item("URL",      data["repo_url"])
    item("Scanned",  data["scanned_at"])
    item("Files",    f"{data['file_count']:,}")
    item("Dirs",     f"{data['dir_count']:,}")

    section("Languages detected")
    if data["languages"]:
        for lang, count in list(data["languages"].items())[:6]:
            bullet(f"{lang}  ({count} files)")
    else:
        warn("None detected")

    section("Entry points")
    if data["entry_points"]:
        for ep in data["entry_points"][:8]:
            bullet(ep, GREEN)
    else:
        bad("None found")

    section("Routes / views")
    if data["routes"]:
        for r in data["routes"][:8]:
            bullet(r)
        if len(data["routes"]) > 8:
            bullet(f"… and {len(data['routes'])-8} more")
    else:
        warn("None detected")

    section("Config files")
    if data["configs"]:
        for c_ in data["configs"][:8]:
            bullet(c_)
    else:
        bad("None found")

    section("Models / schemas")
    if data["models"]:
        for m in data["models"][:8]:
            bullet(m)
    else:
        warn("None detected")

    section("Tests")
    if data["tests"]:
        for t in data["tests"][:8]:
            bullet(t, GREEN)
    else:
        bad("None found")

    section("Docs")
    if data["docs"]:
        for d in data["docs"]:
            bullet(d, CYAN)
    else:
        bad("None found")

    section("CI / CD")
    if data["ci"]:
        for ci in data["ci"]:
            bullet(ci, GREEN)
    else:
        warn("None detected")

    header("friction")
    section("Onboarding friction analysis")
    for issue in data["friction"]:
        sev = issue["severity"]
        if sev == "positive":
            good(issue["message"])
        elif sev == "high":
            bad(issue["message"])
        elif sev == "medium":
            warn(issue["message"])
        else:
            bullet(issue["message"], YELLOW)

    if data.get("llm_entry_summary"):
        header("llm")
        section(f"Entry point summary  {c('(' + (data.get('llm_provider') or '') + ' / ' + (data.get('llm_model') or '') + ')', DIM)}")
        for line in _wrap(data["llm_entry_summary"], 72):
            click.echo(f"    {line}")

    if data.get("llm_onboarding_report"):
        section("Onboarding narrative")
        for line in _wrap(data["llm_onboarding_report"], 72):
            click.echo(f"    {line}")

    ana = data.get("analysis", {})
    if ana:
        header("analysis")

        arch = ana.get("architecture", {})
        if arch and not arch.get("error"):
            section(f"Architecture  {c('(' + arch.get('confidence','') + ' confidence)', DIM)}")
            click.echo(f"    {c(arch.get('label','?'), BOLD, CYAN)}  —  {arch.get('description','')[:80]}")
            cands = arch.get("candidates", [])[1:4]
            if cands:
                click.echo(c(f"    Also matches: {', '.join(a['label'] for a in cands)}", DIM))

        epc = ana.get("entry_point_confidence", {})
        if epc and not epc.get("error"):
            section("Entry point confidence")
            for ep in epc.get("scored", [])[:6]:
                bar = "█" * (ep["score"] // 10) + "░" * (10 - ep["score"] // 10)
                col = GREEN if ep["confidence"] == "high" else YELLOW if ep["confidence"] == "medium" else RED
                click.echo(f"    {c(bar, col)}  {ep['score']:3d}  {ep['path']}")
                if ep.get("signals"):
                    click.echo(c(f"          {', '.join(ep['signals'][:3])}", DIM))

        fdp = ana.get("first_day_path", {})
        if fdp and not fdp.get("error"):
            section(f"First day path  {c('(' + str(fdp.get('completeness', 0)) + '% automatable)', DIM)}")
            for step in fdp.get("steps", []):
                icon = c("✓", GREEN) if step["status"] == "found" else c("✗", RED) if step["status"] == "missing" else c("?", YELLOW)
                click.echo(f"    {icon}  {c(str(step['order']).rjust(2), DIM)}  {step['action']}")
                if step.get("target") and step["target"] != "unknown":
                    click.echo(c(f"          → {step['target']}", DIM))
                if step.get("note"):
                    click.echo(c(f"          {step['note']}", DIM))

        naming = ana.get("naming_consistency", {})
        if naming and not naming.get("error"):
            section("Naming consistency")
            if naming.get("overall_consistent"):
                good("Consistent naming style across the codebase")
            else:
                for issue in naming.get("issues", []):
                    warn(issue)
            for label, key in [("Files", "files"), ("Functions", "functions"), ("Classes", "classes")]:
                s = naming.get(key, {})
                if s and s.get("dominant"):
                    click.echo(f"    {c(label+':', DIM)}  dominant={c(s['dominant'], CYAN)}  clash={s.get('clash_pct',0)}%")

        routes_rich = ana.get("route_detection", {})
        if routes_rich and not routes_rich.get("error") and routes_rich.get("total", 0) > 0:
            section(f"Routes detected  {c('(' + str(routes_rich['total']) + ' total)', DIM)}")
            by_method = routes_rich.get("by_method", {})
            if by_method:
                method_str = "  ".join(f"{c(m, CYAN)}:{n}" for m, n in sorted(by_method.items()))
                click.echo(f"    {method_str}")
            for fw, fw_routes in list(routes_rich.get("by_framework", {}).items())[:3]:
                click.echo(c(f"\n    [{fw}]", BOLD))
                for r in fw_routes[:5]:
                    click.echo(f"    {c(r['method'].ljust(7), YELLOW)} {r['path']}")
                if len(fw_routes) > 5:
                    click.echo(c(f"    … and {len(fw_routes)-5} more", DIM))

        params = ana.get("parameter_tracking", {})
        if params and not params.get("error"):
            section(f"Parameter tracking  {c('(' + str(params.get('env_var_count',0)) + ' env vars found)', DIM)}")
            undoc = params.get("undocumented_env_vars", [])
            if undoc:
                warn(f"{len(undoc)} env var(s) used in code but NOT in .env.example:")
                for u in undoc[:6]:
                    bullet(f"{u['var']}  ({len(u['referenced_in'])} file(s))", RED)
            else:
                good("All detected env vars appear to be documented")
            if params.get("cli_flags"):
                click.echo(c(f"    CLI flags: {', '.join(f['flag'] for f in params['cli_flags'][:8])}", DIM))

        dep_map = ana.get("dependency_impact", {})
        if dep_map and not dep_map.get("error") and dep_map.get("deps"):
            section(f"Dependency impact  {c('(' + str(dep_map.get('total',0)) + ' deps, ' + str(dep_map.get('referenced_count',0)) + ' referenced in code)', DIM)}")
            for d in dep_map.get("high_impact", [])[:5]:
                click.echo(f"    {c(d['name'].ljust(22), CYAN)}  {c(str(d['file_count']).rjust(3)+' files', YELLOW)}  {', '.join(d['dirs_affected'][:3])}")
            unused = dep_map.get("possibly_unused", [])
            if unused:
                click.echo(c(f"    Possibly unused: {', '.join(unused[:8])}", DIM))

        hc = ana.get("hidden_complexity", {})
        if hc and not hc.get("error"):
            findings = hc.get("findings", [])
            section(f"Hidden complexity  {c('(' + str(len(findings)) + ' signal(s) found)', DIM)}")
            if not findings:
                good("No significant hidden complexity signals detected")
            for f in findings[:8]:
                sev_col = RED if f["severity"] == "high" else YELLOW if f["severity"] == "medium" else DIM
                click.echo(f"    {c('['+f['severity']+']', sev_col).ljust(20)}  {f['description'][:65]}")
                click.echo(c(f"          {f['file_count']} file(s), {f['total_occurrences']} occurrence(s)", DIM))

        flow = ana.get("flow_trace", {})
        if flow and not flow.get("error") and flow.get("node_count", 0) > 0:
            section(f"Flow trace  {c('(' + str(flow['node_count']) + ' nodes, ' + str(flow['edge_count']) + ' edges)', DIM)}")
            for ep in flow.get("entry_points_traced", []):
                click.echo(f"    {c('▶', CYAN)} {ep}")
                children = flow.get("graph", {}).get(ep, [])
                for ch in children[:4]:
                    click.echo(c(f"      └─ {ch}", DIM))
                if len(children) > 4:
                    click.echo(c(f"      └─ … {len(children)-4} more", DIM))
            most_imp = flow.get("most_imported", [])
            if most_imp:
                click.echo(c(f"\n    Most-imported modules:", DIM))
                for m in most_imp[:4]:
                    click.echo(c(f"      {str(m['import_count']).rjust(3)}× {m['file']}", DIM))

    # Score
    click.echo()
    click.echo(c("  ┌──────────────────────────────────┐", DIM))
    click.echo(c("  │", DIM) + f"  Onboarding score: " +
               c(f"{score['value']}/100", BOLD, score_color) +
               f"  ({score['label']})" +
               c("         │", DIM))
    click.echo(c("  └──────────────────────────────────┘", DIM))
    click.echo()


def write_json_report(data: dict, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    click.echo(c(f"  ✓ JSON report  → {output_path}", GREEN))


def write_markdown_report(data: dict, output_path: str):
    score = data["score"]
    score_emoji = "🟢" if score["value"] >= 75 else "🟡" if score["value"] >= 50 else "🔴"

    lines = [
        f"# Onboarding Friction Report",
        f"",
        f"> Generated by **repomap** on {data['scanned_at']}",
        f"",
        f"## Repository",
        f"",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| URL | {data['repo_url']} |",
        f"| Files | {data['file_count']:,} |",
        f"| Directories | {data['dir_count']:,} |",
        f"| Languages | {', '.join(list(data['languages'].keys())[:5]) or 'N/A'} |",
        f"",
        f"## Onboarding Score",
        f"",
        f"{score_emoji} **{score['value']}/100** — {score['label']}",
        f"",
        f"## Friction Issues",
        f"",
    ]

    highs   = [i for i in data["friction"] if i["severity"] == "high"]
    mediums = [i for i in data["friction"] if i["severity"] == "medium"]
    lows    = [i for i in data["friction"] if i["severity"] == "low"]
    positives = [i for i in data["friction"] if i["severity"] == "positive"]

    if highs:
        lines.append("### 🔴 High Priority")
        lines.append("")
        for i in highs:
            lines.append(f"- {i['message']}")
        lines.append("")

    if mediums:
        lines.append("### 🟡 Medium Priority")
        lines.append("")
        for i in mediums:
            lines.append(f"- {i['message']}")
        lines.append("")

    if lows:
        lines.append("### ⚪ Low Priority / Notes")
        lines.append("")
        for i in lows:
            lines.append(f"- {i['message']}")
        lines.append("")

    if positives:
        lines.append("### ✅ Positives")
        lines.append("")
        for i in positives:
            lines.append(f"- {i['message']}")
        lines.append("")

    # LLM sections
    if data.get("llm_entry_summary") or data.get("llm_onboarding_report"):
        provider_label = f"{data.get('llm_provider', '')} / {data.get('llm_model', '')}".strip(" /")
        lines += [
            f"## 🤖 AI Analysis",
            f"",
            f"_Powered by {provider_label}_",
            f"",
        ]
        if data.get("llm_entry_summary"):
            lines += [
                "### Entry Point Summary",
                "",
                data["llm_entry_summary"],
                "",
            ]
        if data.get("llm_onboarding_report"):
            lines += [
                "### Onboarding Narrative",
                "",
                data["llm_onboarding_report"],
                "",
            ]

    def section_md(title, items, fallback="None detected."):
        out = [f"## {title}", ""]
        if items:
            for it in items[:15]:
                out.append(f"- `{it}`")
            if len(items) > 15:
                out.append(f"- … and {len(items)-15} more")
        else:
            out.append(f"_{fallback}_")
        out.append("")
        return out

    lines += section_md("Entry Points",       data["entry_points"], "No entry points found.")
    lines += section_md("Routes / Views",     data["routes"],       "No routes detected.")
    lines += section_md("Config Files",       data["configs"],      "No config files found.")
    lines += section_md("Models / Schemas",   data["models"],       "No models detected.")
    lines += section_md("Tests",              data["tests"],        "No tests found.")
    lines += section_md("Documentation",      data["docs"],         "No docs found.")
    lines += section_md("CI / CD",            data["ci"],           "No CI config detected.")

    ana = data.get("analysis", {})
    if ana:
        lines += ["", "---", "## 🔬 Deep Analysis", ""]

        arch = ana.get("architecture", {})
        if arch and not arch.get("error"):
            lines += [
                "### Architecture",
                "",
                f"**{arch.get('label', '?')}** *(confidence: {arch.get('confidence', '?')})*",
                "",
                arch.get("description", ""),
                "",
            ]
            cands = arch.get("candidates", [])[1:4]
            if cands:
                lines.append(f"_Also matches: {', '.join(a['label'] for a in cands)}_")
                lines.append("")

        epc = ana.get("entry_point_confidence", {})
        if epc and not epc.get("error") and epc.get("scored"):
            lines += ["### Entry Point Confidence", "",
                      "| Score | Confidence | File | Signals |",
                      "|-------|-----------|------|---------|"]
            for ep in epc.get("scored", [])[:8]:
                sigs = ", ".join(ep.get("signals", [])[:3])
                lines.append(f"| {ep['score']} | {ep['confidence']} | `{ep['path']}` | {sigs} |")
            lines.append("")

        fdp = ana.get("first_day_path", {})
        if fdp and not fdp.get("error"):
            lines += [f"### First Day Path *(~{fdp.get('completeness',0)}% automatable)*", ""]
            for step in fdp.get("steps", []):
                icon = "✅" if step["status"] == "found" else "❌" if step["status"] == "missing" else "⚠️"
                lines.append(f"{icon} **{step['order']}. {step['action']}**")
                if step.get("target") and step["target"] not in ("unknown", ""):
                    lines.append(f"   ```\n   {step['target']}\n   ```")
                if step.get("note"):
                    lines.append(f"   _{step['note']}_")
                lines.append("")

        naming = ana.get("naming_consistency", {})
        if naming and not naming.get("error"):
            lines += ["### Naming Consistency", ""]
            if naming.get("overall_consistent"):
                lines.append("✅ Consistent naming style across the codebase.")
            else:
                for issue in naming.get("issues", []):
                    lines.append(f"- ⚠️ {issue}")
            lines.append("")
            lines += ["| Scope | Dominant Style | Clash % |",
                      "|-------|---------------|---------|"]
            for label, key in [("Files", "files"), ("Directories", "dirs"),
                                ("Functions", "functions"), ("Classes", "classes")]:
                s = naming.get(key, {})
                if s and s.get("dominant"):
                    lines.append(f"| {label} | `{s['dominant']}` | {s.get('clash_pct',0)}% |")
            lines.append("")

        routes_rich = ana.get("route_detection", {})
        if routes_rich and not routes_rich.get("error") and routes_rich.get("total", 0) > 0:
            lines += [f"### Routes Detected ({routes_rich['total']} total)", ""]
            by_m = routes_rich.get("by_method", {})
            if by_m:
                lines.append("**By method:** " + "  ".join(f"`{m}:{n}`" for m, n in sorted(by_m.items())))
                lines.append("")
            lines += ["| Method | Path | File | Framework |",
                      "|--------|------|------|-----------|"]
            for r in routes_rich.get("routes", [])[:25]:
                lines.append(f"| `{r['method']}` | `{r['path']}` | `{r['file']}` | {r['framework']} |")
            if routes_rich["total"] > 25:
                lines.append(f"\n_… and {routes_rich['total']-25} more routes_")
            lines.append("")

        params = ana.get("parameter_tracking", {})
        if params and not params.get("error"):
            lines += [f"### Parameter Tracking ({params.get('env_var_count',0)} env vars)", ""]
            undoc = params.get("undocumented_env_vars", [])
            if undoc:
                lines.append(f"⚠️ **{len(undoc)} undocumented env var(s)** — used in code but missing from `.env.example`:")
                lines.append("")
                for u in undoc[:10]:
                    files_str = ", ".join(f"`{f}`" for f in u["referenced_in"][:3])
                    lines.append(f"- `{u['var']}` — {files_str}")
            else:
                lines.append("✅ All detected env vars appear documented.")
            if params.get("env_vars"):
                lines += ["", "**All env vars:**", ""]
                lines += ["| Variable | Files | Documented |", "|----------|-------|-----------|"]
                for v in params["env_vars"][:20]:
                    doc = "✅" if v["documented"] else "❌"
                    lines.append(f"| `{v['var']}` | {len(v['referenced_in'])} | {doc} |")
            lines.append("")

        dep_map = ana.get("dependency_impact", {})
        if dep_map and not dep_map.get("error") and dep_map.get("deps"):
            lines += [f"### Dependency Impact ({dep_map.get('total',0)} declared, {dep_map.get('referenced_count',0)} referenced)", ""]
            lines += ["| Package | Version | Files | Impact | Dirs |",
                      "|---------|---------|-------|--------|------|"]
            for d in dep_map.get("deps", [])[:20]:
                dirs_str = ", ".join(d["dirs_affected"][:3])
                lines.append(f"| `{d['name']}` | `{d['version']}` | {d['file_count']} | {d['impact']} | {dirs_str} |")
            unused = dep_map.get("possibly_unused", [])
            if unused:
                lines.append(f"\n_Possibly unused: {', '.join(f'`{u}`' for u in unused[:10])}_")
            lines.append("")

        hc = ana.get("hidden_complexity", {})
        if hc and not hc.get("error"):
            findings = hc.get("findings", [])
            lines += [f"### Hidden Complexity ({len(findings)} signals)", ""]
            if not findings:
                lines.append("✅ No significant hidden complexity detected.")
            else:
                lines += ["| Signal | Severity | Files | Description |",
                          "|--------|----------|-------|-------------|"]
                for f in findings:
                    lines.append(f"| `{f['signal']}` | {f['severity']} | {f['file_count']} | {f['description'][:60]} |")
            lines.append("")

        flow = ana.get("flow_trace", {})
        if flow and not flow.get("error") and flow.get("node_count", 0) > 0:
            lines += [f"### Flow Trace ({flow['node_count']} nodes, {flow['edge_count']} edges)", ""]
            for ep in flow.get("entry_points_traced", []):
                lines.append(f"**`{ep}`**")
                children = flow.get("graph", {}).get(ep, [])
                for ch in children[:6]:
                    lines.append(f"- `{ch}`")
                if len(children) > 6:
                    lines.append(f"- _… {len(children)-6} more_")
                lines.append("")
            most_imp = flow.get("most_imported", [])
            if most_imp:
                lines += ["**Most-imported modules:**", ""]
                for m in most_imp[:6]:
                    lines.append(f"- `{m['file']}` — imported {m['import_count']}×")
                lines.append("")

    lines += [
        "## Languages",
        "",
    ]
    if data["languages"]:
        lines.append("| Language | Files |")
        lines.append("|----------|-------|")
        for lang, count in data["languages"].items():
            lines.append(f"| {lang} | {count} |")
    else:
        lines.append("_None detected._")

    lines += [
        "",
        "---",
        "_Generated by [repomap](https://github.com/) — paste a repo URL, get a first-pass onboarding friction report._",
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    click.echo(c(f"  ✓ Markdown report → {output_path}", GREEN))


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
@click.command()
@click.argument("repo_url")
@click.option("--output-dir", "-o", default=".", show_default=True,
              help="Directory to write JSON and Markdown reports.")
@click.option("--keep-clone", is_flag=True, default=False,
              help="Keep the cloned repo after analysis.")
@click.option("--no-color", is_flag=True, default=False,
              help="Disable ANSI colour output.")
@click.option("--json-only", is_flag=True, default=False,
              help="Skip terminal report, only write files.")
@click.option("--llm", is_flag=True, default=False,
              help="Enable LLM-powered entry point summarization (auto-detects provider).")
@click.option("--llm-provider", default=None, show_default=True,
              type=click.Choice(["anthropic", "openai", "ollama", "openai-compat"],
                                case_sensitive=False),
              help="LLM provider to use. Auto-detected from env if not set.")
@click.option("--llm-api-key", default=None, envvar="REPOMAP_LLM_API_KEY",
              help="API key. Falls back to ANTHROPIC_API_KEY / OPENAI_API_KEY / etc.")
@click.option("--llm-model", default=None,
              help="Model name override (e.g. claude-3-5-sonnet-20241022, gpt-4o).")
@click.option("--llm-base-url", default=None,
              help="Base URL for openai-compat or custom Ollama host.")
@click.option("--llm-timeout", default=60, show_default=True,
              help="LLM request timeout in seconds.")
@click.option("--llm-full-report", is_flag=True, default=False,
              help="Also generate an LLM onboarding narrative (uses more tokens).")
@click.option("--analyze/--no-analyze", default=True, show_default=True,
              help="Run deep analysis passes (on by default).")
@click.option("--analyze-only", default=None, multiple=True,
              metavar="NAME",
              help="Run only specific analyzers. Repeatable. Names: "
                   "first_day_path, naming_consistency, entry_point_confidence, "
                   "flow_trace, parameter_tracking, route_detection, "
                   "dependency_impact, hidden_complexity, architecture")
@click.option("--symbols", is_flag=True, default=False,
              help="Build cross-file symbol map (functions, classes, variables "
                   "that cross file boundaries). Writes <slug>_symbols.json and "
                   "<slug>_symbols.md.")
@click.option("--symbols-min-files", default=2, show_default=True,
              help="Only include symbols used in this many or more files.")
@click.option("--serve", is_flag=True, default=False,
              help="After analysis, open an interactive HTML dashboard in your browser.")
@click.option("--port", default=7878, show_default=True,
              help="Port for the dashboard server (used with --serve).")
@click.option("--no-open", is_flag=True, default=False,
              help="With --serve: start server but don't auto-open the browser.")
def main(repo_url, output_dir, keep_clone, no_color, json_only,
         llm, llm_provider, llm_api_key, llm_model, llm_base_url,
         llm_timeout, llm_full_report, analyze, analyze_only,
         symbols, symbols_min_files, serve, port, no_open):
    """
    repomap v3 — paste a REPO_URL, get a first-pass onboarding friction report.

    \b
    Outputs:
      • terminal summary
      • <output_dir>/repomap_<slug>.json
      • <output_dir>/repomap_<slug>.md

    \b
    Dashboard (opens in browser):
      repomap https://github.com/pallets/flask --serve
      repomap https://github.com/pallets/flask --serve --symbols
      repomap https://github.com/pallets/flask --serve --port 9090 --no-open

    \b
    Deep analysis (on by default):
      repomap https://github.com/pallets/flask
      repomap https://github.com/django/django --no-analyze  (skip deep analysis)
      repomap https://github.com/pallets/flask --analyze-only architecture
      repomap https://github.com/pallets/flask --analyze-only hidden_complexity --analyze-only route_detection

    \b
    LLM examples:
      repomap https://github.com/pallets/flask --llm
      repomap https://github.com/pallets/flask --llm --llm-provider anthropic
      repomap https://github.com/django/django --llm --llm-provider openai --llm-model gpt-4o
      repomap https://github.com/fastapi/fastapi --llm --llm-provider ollama --llm-model llama3
      repomap https://github.com/pallets/flask --llm --llm-full-report

    \b
    Provider auto-detection order:
      ANTHROPIC_API_KEY → OPENAI_API_KEY → GROQ/TOGETHER/MISTRAL/FIREWORKS → Ollama
    """
    if no_color:
        global RESET, BOLD, DIM, GREEN, YELLOW, CYAN, RED, BLUE, MAGENTA
        RESET = BOLD = DIM = GREEN = YELLOW = CYAN = RED = BLUE = MAGENTA = ""

    if not re.match(r"https?://", repo_url):
        click.echo(c(f"Error: '{repo_url}' doesn't look like a valid URL.", RED), err=True)
        sys.exit(1)

    # Resolve LLM config early so we can fail fast before cloning
    llm_cfg = None
    if llm or llm_provider:
        if not LLM_AVAILABLE:
            click.echo(c("Error: llm.py not found next to repomap.py.", RED), err=True)
            sys.exit(1)
        try:
            llm_cfg = _llm.build_config(
                provider=llm_provider,
                api_key=llm_api_key,
                model=llm_model,
                base_url=llm_base_url,
                timeout=llm_timeout,
            )
            click.echo()
            click.echo(c(f"  ◆ LLM provider: {llm_cfg.provider}  model: {llm_cfg.model}", BOLD, MAGENTA))
        except _llm.LLMNotConfigured as e:
            click.echo(c(f"\nLLM not configured:\n{e}", RED), err=True)
            sys.exit(1)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir    = tempfile.mkdtemp(prefix="repomap_")
    clone_path = Path(tmp_dir) / "repo"

    try:
        click.echo()
        click.echo(c(f"  ◆ Cloning {repo_url}", BOLD, CYAN))
        click.echo(c(f"    → {clone_path}", DIM))

        result = subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", repo_url, str(clone_path)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            click.echo(c(f"\nGit clone failed:\n{result.stderr}", RED), err=True)
            sys.exit(1)
        good("Clone complete")

        click.echo(c("\n  ◆ Scanning repository…", BOLD, CYAN))
        scanner = RepoScanner(clone_path, repo_url)
        data    = scanner.scan()
        good(f"Scanned {data['file_count']:,} files across {data['dir_count']:,} directories")

        if analyze and ANALYZERS_AVAILABLE:
            selected = list(analyze_only) if analyze_only else None
            label = f"({', '.join(selected)})" if selected else "(all passes)"
            click.echo(c(f"\n  ◆ Running deep analysis {label}…", BOLD, BLUE))
            _analyzers.run_all(clone_path, data, selected=selected)
            passes_run = len(data.get("analysis", {}))
            good(f"{passes_run} analysis pass(es) complete")
        elif analyze and not ANALYZERS_AVAILABLE:
            warn("analyzers.py not found — skipping deep analysis")

        if llm_cfg:
            click.echo(c("\n  ◆ Running LLM analysis…", BOLD, MAGENTA))
            try:
                click.echo(c("    → reading entry point files…", DIM))
                file_contents = scanner.read_entry_point_contents()
                if file_contents:
                    click.echo(c(f"    → summarizing {len(file_contents)} file(s) with {llm_cfg.provider}…", DIM))
                    data["llm_entry_summary"] = _llm.summarize_entry_points(
                        llm_cfg, repo_url, data, file_contents)
                    data["llm_provider"] = llm_cfg.provider
                    data["llm_model"]    = llm_cfg.model
                    good("Entry point summary complete")
                else:
                    warn("No readable entry point files found for LLM analysis")
            except _llm.LLMError as e:
                warn(f"LLM entry summary failed: {e}")

            if llm_full_report:
                try:
                    click.echo(c("    → generating onboarding narrative…", DIM))
                    data["llm_onboarding_report"] = _llm.summarize_full_report(
                        llm_cfg, repo_url, data)
                    data["llm_provider"] = llm_cfg.provider
                    data["llm_model"]    = llm_cfg.model
                    good("Onboarding narrative complete")
                except _llm.LLMError as e:
                    warn(f"LLM full report failed: {e}")

        if not json_only:
            print_terminal_report(data)

        section("Writing reports")
        slug = re.sub(r"[^a-z0-9]+", "_", repo_url.lower().split("github.com/")[-1]).strip("_")
        base = f"repomap_{slug}" if slug else "repomap_report"

        json_path = output_dir / f"{base}.json"
        md_path   = output_dir / f"{base}.md"

        write_json_report(data, str(json_path))
        write_markdown_report(data, str(md_path))

        if symbols:
            if not SG_AVAILABLE:
                warn("symbol_graph.py not found — skipping symbol map")
            else:
                click.echo(c(f"\n  ◆ Building cross-file symbol graph…", BOLD, CYAN))
                sg = _sg.build_symbol_graph(clone_path)

                if symbols_min_files > 1:
                    sg["symbols"] = [s for s in sg["symbols"]
                                     if s["file_count"] >= symbols_min_files]
                    # rebuild by_file index after filter
                    from collections import defaultdict as _dd
                    bf: dict = _dd(list)
                    for s in sg["symbols"]:
                        bf[s["defined_in"]].append(s)
                    sg["by_file"] = dict(bf)
                    sg["total_symbols"] = len(sg["symbols"])

                good(f"{sg['total_symbols']} cross-file symbol(s) found "
                     f"(used in ≥{symbols_min_files} file(s))")

                if not json_only:
                    _sg.print_symbol_summary(sg)

                sym_json_path = output_dir / f"{base}_symbols.json"
                sym_md_path   = output_dir / f"{base}_symbols.md"
                _sg.write_symbol_json(sg,               str(sym_json_path))
                _sg.write_symbol_markdown(sg, repo_url, str(sym_md_path))

        click.echo()

        if keep_clone:
            dest = output_dir / "repo_clone"
            shutil.copytree(clone_path, dest, dirs_exist_ok=True)
            click.echo(c(f"  ✓ Repo clone kept → {dest}", GREEN))

        if serve:
            if not RS_AVAILABLE:
                warn("report_server.py not found — cannot start dashboard")
            else:
                sym_json = str(output_dir / f"{base}_symbols.json") if symbols else None
                # Check the symbols file actually exists
                if sym_json and not Path(sym_json).exists():
                    sym_json = None
                _rs.serve(
                    str(json_path),
                    symbols_path=sym_json,
                    port=port,
                    open_browser=not no_open,
                )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

