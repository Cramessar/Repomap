"""
analyzers.py — repomap v3 deep-analysis passes.

Each analyzer is a pure function:
    run(repo_path: Path, scan_data: dict) -> dict

They are imported and called by repomap.py after the base scan,
and their results are merged into the main data dict under
data["analysis"][<key>].

Analyzers
─────────
1.  first_day_path          — ordered checklist of first-day steps
2.  naming_consistency      — detects style clashes across files/dirs
3.  entry_point_confidence  — scores each entry point 0-100 with rationale
4.  flow_trace              — traces call/import chains from entry points
5.  parameter_tracking      — env vars, CLI flags, config keys referenced in code
6.  route_detection         — rich route extraction with method + path + file
7.  dependency_impact       — top deps + which files/dirs depend on them
8.  hidden_complexity       — signals of non-obvious complexity
9.  architecture_classify   — identifies the overall architectural pattern
"""

from __future__ import annotations

import ast
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".env", "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".tox", "eggs", ".eggs",
}

def _safe_read(path: Path, max_bytes: int = 200_000) -> Optional[str]:
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(errors="ignore")
    except Exception:
        return None

def _rel(repo_path: Path, p: Path) -> str:
    return str(p.relative_to(repo_path))

def _walk_source(repo_path: Path, exts: set[str]) -> list[Path]:
    """Walk repo, yield source files with given extensions, skipping junk dirs."""
    out = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for f in files:
            p = Path(root) / f
            if p.suffix in exts:
                out.append(p)
    return out


# ─────────────────────────────────────────────────────────────
# 1. FIRST DAY PATH
# ─────────────────────────────────────────────────────────────
def first_day_path(repo_path: Path, scan_data: dict) -> dict:
    """
    Build an ordered checklist of the first things a new dev should do.
    Each step is {order, action, target, status, note}.
    Status: "found" | "missing" | "manual"
    """
    steps = []
    order = 1

    def step(action: str, target: str, status: str, note: str = ""):
        nonlocal order
        steps.append({"order": order, "action": action, "target": target,
                       "status": status, "note": note})
        order += 1

    readme = next((d for d in scan_data.get("docs", []) if "README" in d.upper()), None)
    step("Read README", readme or "README.md", "found" if readme else "missing",
         "Start here for project overview." if readme else "No README found — onboarding is blind.")

    langs = list(scan_data.get("languages", {}).keys())
    runtime_map = {
        "Python": "python3 --version", "JavaScript": "node --version",
        "TypeScript": "node --version && npx tsc --version",
        "Go": "go version", "Ruby": "ruby --version",
        "Rust": "rustc --version", "Java": "java -version",
        "Kotlin": "java -version", "C#": "dotnet --version",
    }
    for lang in langs[:3]:
        cmd = runtime_map.get(lang)
        if cmd:
            step(f"Verify {lang} runtime", cmd, "manual",
                 f"Ensure {lang} is installed before continuing.")

    env_example = next((c for c in scan_data.get("configs", []) if ".env" in c), None)
    if env_example:
        step("Copy env file", f"cp {env_example} .env", "found",
             "Fill in secrets/credentials before running.")
    else:
        step("Create .env", ".env", "missing",
             "No .env.example found — check docs or ask a teammate for required vars.")

    configs = scan_data.get("configs", [])
    dep_cmds = []
    if any("requirements" in c for c in configs):
        dep_cmds.append("pip install -r requirements.txt")
    if any("pyproject.toml" in c for c in configs):
        dep_cmds.append("pip install -e .")
    if any("package.json" in c and "lock" not in c for c in configs):
        lockfile = "yarn.lock" if any("yarn.lock" in c for c in configs) else None
        dep_cmds.append("yarn install" if lockfile else "npm install")
    if any("Pipfile" in c for c in configs):
        dep_cmds.append("pipenv install")
    if any("go.mod" in c for c in configs):
        dep_cmds.append("go mod download")
    if any("Cargo.toml" in c for c in configs):
        dep_cmds.append("cargo build")

    if dep_cmds:
        step("Install dependencies", dep_cmds[0], "found",
             " OR ".join(dep_cmds) if len(dep_cmds) > 1 else "")
    else:
        step("Install dependencies", "unknown", "manual", "Dependency manager not detected.")

    has_compose = any("docker-compose" in e for e in scan_data.get("entry_points", []))
    if has_compose:
        step("Start services", "docker-compose up -d", "found",
             "Brings up databases, caches, and other services.")

    has_migrations = any("migration" in m.lower() for m in scan_data.get("models", []))
    if has_migrations:
        manage_py = any("manage.py" in e for e in scan_data.get("entry_points", []))
        if manage_py:
            step("Run migrations", "python manage.py migrate", "found", "Django migrations.")
        else:
            step("Run migrations", "alembic upgrade head  (or equivalent)", "manual",
                 "Migration framework detected — check docs for exact command.")

    entry_points = scan_data.get("entry_points", [])
    run_cmd = None
    if any("manage.py" in e for e in entry_points):
        run_cmd = "python manage.py runserver"
    elif any("main.py" in e for e in entry_points):
        run_cmd = "python main.py  (or: uvicorn main:app --reload)"
    elif any("app.py" in e for e in entry_points):
        run_cmd = "python app.py  (or: flask run)"
    elif any(e.endswith("index.js") or e.endswith("server.js") for e in entry_points):
        run_cmd = "node index.js  (or: npm start)"
    elif any(e.endswith("index.ts") or e.endswith("server.ts") for e in entry_points):
        run_cmd = "npx ts-node index.ts  (or: npm run dev)"
    elif any("Makefile" in e for e in entry_points):
        run_cmd = "make  (check Makefile for targets)"
    elif has_compose:
        run_cmd = "docker-compose up"

    step("Run the project", run_cmd or "unknown — check README", "found" if run_cmd else "manual",
         "Start the application.")

    tests = scan_data.get("tests", [])
    if tests:
        if any(".py" in t for t in tests) or any("test" in t for t in tests):
            step("Run tests", "pytest  (or: python -m pytest)", "found",
                 "Verify the project is working correctly.")
        elif any(".ts" in t or ".js" in t for t in tests):
            step("Run tests", "npm test  (or: npx jest)", "found", "")
        else:
            step("Run tests", "check package.json scripts for test command", "manual", "")
    else:
        step("Run tests", "no test suite found", "missing",
             "No tests detected — explore manually.")
    has_contributing = any("CONTRIBUTING" in d.upper() for d in scan_data.get("docs", []))
    step("Read contribution guide", "CONTRIBUTING.md",
         "found" if has_contributing else "missing",
         "Understand PR process, branch strategy, and code standards." if has_contributing
         else "No CONTRIBUTING.md — ask the team about workflow.")

    return {
        "steps": steps,
        "completeness": round(
            sum(1 for s in steps if s["status"] == "found") / len(steps) * 100
        ) if steps else 0,
    }


# ─────────────────────────────────────────────────────────────
# 2. NAMING CONSISTENCY CHECK
# ─────────────────────────────────────────────────────────────
_SNAKE = re.compile(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$')
_CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]+$')
_PASCAL= re.compile(r'^[A-Z][a-zA-Z0-9]+$')
_KEBAB = re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')
_UPPER = re.compile(r'^[A-Z][A-Z0-9_]+$')

def _style_of(name: str) -> str:
    if _SNAKE.match(name):  return "snake_case"
    if _CAMEL.match(name):  return "camelCase"
    if _PASCAL.match(name): return "PascalCase"
    if _KEBAB.match(name):  return "kebab-case"
    if _UPPER.match(name):  return "UPPER_CASE"
    return "mixed/other"

def naming_consistency(repo_path: Path, scan_data: dict) -> dict:
    """
    Sample file names and directory names to detect style clashes.
    Returns dominant style, minority styles, and examples of each.
    """
    file_styles: dict[str, list[str]] = defaultdict(list)
    dir_styles:  dict[str, list[str]] = defaultdict(list)
    py_func_styles: dict[str, list[str]] = defaultdict(list)
    py_class_styles: dict[str, list[str]] = defaultdict(list)

    source_exts = {".py", ".js", ".ts", ".go", ".rb", ".java", ".rs"}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        rp = Path(root)

        for d in dirs:
            stem = d.split(".")[0]
            if len(stem) > 2:
                dir_styles[_style_of(stem)].append(str((rp / d).relative_to(repo_path)))

        for fname in files:
            p = rp / fname
            stem = p.stem
            if p.suffix in source_exts and len(stem) > 2:
                file_styles[_style_of(stem)].append(str(p.relative_to(repo_path)))

            if p.suffix == ".py" and p.stat().st_size < 100_000:
                text = _safe_read(p)
                if text:
                    try:
                        tree = ast.parse(text, filename=str(p))
                        for node in ast.walk(tree):
                            if isinstance(node, ast.FunctionDef):
                                if len(node.name) > 2:
                                    py_func_styles[_style_of(node.name)].append(
                                        f"{str(p.relative_to(repo_path))}::{node.name}")
                            elif isinstance(node, ast.ClassDef):
                                if len(node.name) > 2:
                                    py_class_styles[_style_of(node.name)].append(
                                        f"{str(p.relative_to(repo_path))}::{node.name}")
                    except SyntaxError:
                        pass

    def summarise(styles: dict[str, list[str]], label: str) -> dict:
        if not styles:
            return {}
        total = sum(len(v) for v in styles.values())
        dominant = max(styles, key=lambda k: len(styles[k]))
        result: dict = {
            "dominant": dominant,
            "breakdown": {k: {"count": len(v), "pct": round(len(v)/total*100),
                               "examples": v[:3]}
                          for k, v in sorted(styles.items(), key=lambda x: -len(x[1]))},
        }
        # flag if >10% of names are NOT in the dominant style
        minority_count = total - len(styles[dominant])
        result["consistent"] = (minority_count / total) < 0.10
        result["clash_pct"]  = round(minority_count / total * 100)

        clashes = [k for k in styles if k != dominant and len(styles[k]) > 2]
        if clashes:
            result["clashing_styles"] = {k: styles[k][:3] for k in clashes}
        return result

    issues = []
    files_summary   = summarise(file_styles,     "files")
    dirs_summary    = summarise(dir_styles,      "dirs")
    funcs_summary   = summarise(py_func_styles,  "functions")
    classes_summary = summarise(py_class_styles, "classes")

    for label, s in [("Files", files_summary), ("Directories", dirs_summary),
                     ("Functions", funcs_summary), ("Classes", classes_summary)]:
        if s and not s.get("consistent") and s.get("clash_pct", 0) > 15:
            clashing = list(s.get("clashing_styles", {}).keys())
            clash_str = ", ".join(clashing) if clashing else "mixed styles"
            issues.append(
                f"{label}: dominant style is {s['dominant']} but "
                f"{s['clash_pct']}% use {clash_str}"
            )

    return {
        "files":    files_summary,
        "dirs":     dirs_summary,
        "functions": funcs_summary,
        "classes":  classes_summary,
        "issues":   issues,
        "overall_consistent": len(issues) == 0,
    }


# ─────────────────────────────────────────────────────────────
# 3. ENTRY POINT CONFIDENCE SCORING
# ─────────────────────────────────────────────────────────────
def _score_entry(path: str, repo_path: Path) -> dict:
    """Score a single file as an entry point. Returns score 0-100 + signals."""
    signals = []
    score   = 0
    full    = repo_path / path

    name = Path(path).name
    depth = path.replace("\\", "/").count("/")

    if name in ("main.py", "app.py", "server.py", "index.js", "index.ts",
                "main.go", "main.ts", "__main__.py"):
        score += 40; signals.append("canonical entry point filename")
    elif name in ("manage.py", "cli.py", "wsgi.py", "asgi.py", "run.py"):
        score += 35; signals.append("framework entry filename")
    elif name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                  "Makefile", "Procfile"):
        score += 25; signals.append("infrastructure entry file")

    # Root-level files score higher; penalise deep nesting
    if depth == 0:
        score += 20; signals.append("root-level file")
    elif depth == 1:
        score += 10; signals.append("shallow nesting")
    else:
        score -= 5 * (depth - 1); signals.append(f"nested {depth} levels deep")

    if full.exists() and full.suffix in (".py", ".js", ".ts", ".go", ".rb"):
        text = _safe_read(full) or ""
        if re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', text):
            score += 20; signals.append("has __main__ guard")
        if re.search(r'(uvicorn|gunicorn|flask|FastAPI|express|http\.createServer|gin\.New|fiber\.New)', text, re.I):
            score += 15; signals.append("framework bootstrap detected")
        if re.search(r'(app\.run|app\.listen|server\.start|main\(\)|click\.command)', text):
            score += 10; signals.append("explicit start call")
        if re.search(r'argparse|click|typer|cobra|clap|thor', text, re.I):
            score += 10; signals.append("CLI argument parsing")
        if len(text) < 100:
            score -= 15; signals.append("very short file — may be stub")

    score = max(0, min(100, score))
    confidence = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return {"path": path, "score": score, "confidence": confidence, "signals": signals}


def entry_point_confidence(repo_path: Path, scan_data: dict) -> dict:
    scored = [_score_entry(ep, repo_path) for ep in scan_data.get("entry_points", [])]
    scored.sort(key=lambda x: -x["score"])

    primary = [s for s in scored if s["confidence"] == "high"]
    secondary = [s for s in scored if s["confidence"] == "medium"]
    noise = [s for s in scored if s["confidence"] == "low"]

    return {
        "scored": scored,
        "primary":   [s["path"] for s in primary],
        "secondary": [s["path"] for s in secondary],
        "noise":     [s["path"] for s in noise],
        "top_entry": scored[0]["path"] if scored else None,
    }


# ─────────────────────────────────────────────────────────────
# 4. FLOW TRACE
# ─────────────────────────────────────────────────────────────
def _extract_py_imports(text: str) -> list[str]:
    imports = []
    for line in text.splitlines():
        line = line.strip()
        # from X import Y  →  capture X
        m = re.match(r'^from\s+([\w.]+)\s+import\s+', line)
        if m:
            imports.append(m.group(1))
            continue
        # import X, Y, Z  →  capture each name
        m = re.match(r'^import\s+([\w.,\s]+)$', line)
        if m:
            imports.extend(p.strip() for p in m.group(1).split(","))
    return imports

def _extract_js_imports(text: str) -> list[str]:
    imports = []
    for m in re.finditer(r'''(?:import\s+.*?\s+from\s+['"](.+?)['"]|require\(['"](.+?)['"]\))''', text):
        imports.append(m.group(1) or m.group(2))
    return imports

def flow_trace(repo_path: Path, scan_data: dict) -> dict:
    """
    Trace import/require chains from the top entry points.
    Returns a graph: {file -> [imported_local_files]} up to depth 3.
    Also surfaces the files that are imported most (high centrality).
    Windows-safe: all paths normalised to forward slashes internally.
    """
    ep_conf = scan_data.get("analysis", {}).get("entry_point_confidence", {})
    starts  = (ep_conf.get("primary") or ep_conf.get("secondary") or
               scan_data.get("entry_points", []))[:3]

    # Normalise entry point paths to forward slashes
    starts = [p.replace("\\", "/") for p in starts]

    # ── Build Python module index ──────────────────────────
    # Keys: full dotted path, short stem, last-2 and last-3 dotted segments
    # Values: Path objects (platform-native, used only for open())
    py_module_index: dict[str, Path] = {}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = Path(root) / f
            if p.suffix != ".py":
                continue
            # rel is always forward-slash, OS-independent
            rel = p.relative_to(repo_path).as_posix()
            mod_full = rel.removesuffix(".py").replace("/", ".")
            py_module_index[mod_full] = p
            py_module_index[Path(rel).stem] = p
            parts = mod_full.split(".")
            if len(parts) >= 2:
                py_module_index[".".join(parts[-2:])] = p
            if len(parts) >= 3:
                py_module_index[".".join(parts[-3:])] = p

    graph: dict[str, list[str]] = {}
    import_counts: dict[str, int] = defaultdict(int)

    def trace(rel_path: str, depth: int):
        # rel_path is always forward-slash
        rel_path = rel_path.replace("\\", "/")
        if depth > 3 or rel_path in graph:
            return
        full = repo_path / rel_path
        if not full.exists():
            return
        text = _safe_read(full) or ""
        children: list[str] = []

        if full.suffix == ".py":
            # Derive this file's own package prefix for relative resolution
            file_pkg = ".".join(rel_path.removesuffix(".py").split("/")[:-1])
            for imp in _extract_py_imports(text):
                imp = imp.strip()
                parts = imp.split(".")
                candidates = [
                    imp,                          # exact: open_webui.routers.users
                    ".".join(parts[:3]),          # first 3 segments
                    ".".join(parts[:2]),          # first 2 segments
                    parts[0],                     # top-level name
                    f"{file_pkg}.{parts[0]}" if file_pkg else None,   # package-relative
                    f"{file_pkg}.{imp}"        if file_pkg else None,
                ]
                for candidate in candidates:
                    if candidate and candidate in py_module_index:
                        child_p   = py_module_index[candidate]
                        child_rel = child_p.relative_to(repo_path).as_posix()
                        if child_rel != rel_path:
                            children.append(child_rel)
                            import_counts[child_rel] += 1
                        break  # stop at first match per import

        elif full.suffix in (".js", ".ts", ".tsx", ".jsx"):
            for imp in _extract_js_imports(text):
                if not imp.startswith("."):
                    continue  # skip node_modules
                base = (full.parent / imp).resolve()
                for ext in ("", ".ts", ".js", ".tsx", ".jsx", "/index.ts", "/index.js"):
                    candidate = Path(str(base) + ext)
                    if candidate.exists():
                        try:
                            child_rel = candidate.relative_to(repo_path).as_posix()
                            children.append(child_rel)
                            import_counts[child_rel] += 1
                        except ValueError:
                            pass
                        break

        graph[rel_path] = sorted(set(children))
        for child in graph[rel_path]:
            trace(child, depth + 1)

    for ep in starts:
        trace(ep, 0)

    central = sorted(import_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "entry_points_traced": starts,
        "graph": graph,
        "node_count": len(graph),
        "edge_count": sum(len(v) for v in graph.values()),
        "most_imported": [{"file": f, "import_count": n} for f, n in central],
    }


# ─────────────────────────────────────────────────────────────
# 5. PARAMETER TRACKING (light)
# ─────────────────────────────────────────────────────────────
_ENV_PATTERNS = [
    re.compile(r'os\.environ(?:\.get)?\(["\']([A-Z][A-Z0-9_]+)["\']'),
    re.compile(r'os\.getenv\(["\']([A-Z][A-Z0-9_]+)["\']'),
    re.compile(r'process\.env\.([A-Z][A-Z0-9_]+)'),
    re.compile(r'ENV\[["\']([A-Z][A-Z0-9_]+)["\']\]'),
    re.compile(r'getenv\(["\']([A-Z][A-Z0-9_]+)["\']'),
]

_CONFIG_KEY_PATTERNS = [
    re.compile(r'''config(?:uration)?\[['"](\w+)['"]\]'''),
    re.compile(r'''settings\.(\w+)'''),
    re.compile(r'''app\.config\[['"](\w+)['"]\]'''),
]

def parameter_tracking(repo_path: Path, scan_data: dict) -> dict:
    """
    Scan source files for env var references, config key accesses, and CLI flags.
    Returns categorised parameter inventory.
    """
    env_vars:    dict[str, list[str]] = defaultdict(list)
    config_keys: dict[str, list[str]] = defaultdict(list)
    cli_flags:   dict[str, list[str]] = defaultdict(list)

    # Check which env vars are documented in .env.example
    documented_vars: set[str] = set()
    env_example = next(
        (repo_path / c for c in scan_data.get("configs", []) if ".env" in c
         and (repo_path / c).exists()),
        None
    )
    if env_example:
        for line in (env_example.read_text(errors="ignore") if env_example.exists() else "").splitlines():
            m = re.match(r'^([A-Z][A-Z0-9_]+)\s*=', line.strip())
            if m:
                documented_vars.add(m.group(1))

    source_exts = {".py", ".js", ".ts", ".go", ".rb", ".env", ".sh"}
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            p = Path(root) / fname
            if p.suffix not in source_exts:
                continue
            text = _safe_read(p, max_bytes=150_000)
            if not text:
                continue
            rel = str(p.relative_to(repo_path))

            for pat in _ENV_PATTERNS:
                for m in pat.finditer(text):
                    env_vars[m.group(1)].append(rel)

            for pat in _CONFIG_KEY_PATTERNS:
                for m in pat.finditer(text):
                    k = m.group(1)
                    if len(k) > 2:
                        config_keys[k].append(rel)

            for m in re.finditer(r'''(?:add_argument|option|flag)\(['"][-]{1,2}([\w-]+)''', text):
                cli_flags[m.group(1)].append(rel)

    env_sorted = sorted(
        [{"var": k, "referenced_in": sorted(set(v)), "documented": k in documented_vars}
         for k, v in env_vars.items()],
        key=lambda x: (-len(x["referenced_in"]), x["var"])
    )
    undocumented = [e for e in env_sorted if not e["documented"] and
                    any(not r.startswith(".env") for r in e["referenced_in"])]

    return {
        "env_vars": env_sorted,
        "env_var_count": len(env_sorted),
        "undocumented_env_vars": undocumented,
        "config_keys": sorted(
            [{"key": k, "referenced_in": sorted(set(v))[:5]}
             for k, v in config_keys.items()],
            key=lambda x: -len(x["referenced_in"])
        )[:30],
        "cli_flags": sorted(
            [{"flag": k, "defined_in": sorted(set(v))[:3]}
             for k, v in cli_flags.items()],
            key=lambda x: x["flag"]
        )[:30],
        "documented_count": len(documented_vars),
        "documented_vars":  sorted(documented_vars),
    }


# ─────────────────────────────────────────────────────────────
# 6. ROUTE DETECTION (rich)
# ─────────────────────────────────────────────────────────────
# Patterns: (framework, regex, group_mapping)
# group_mapping: dict of {label: group_index}
_ROUTE_REGEXES = [
    # FastAPI / Flask / Starlette — must have @decorator syntax
    ("FastAPI/Flask",
     re.compile(r'@\w+\.(get|post|put|delete|patch|head|options)\s*\(\s*["\']([^"\']+)["\']'),
     {"method": 1, "path": 2}),
    # Express.js — app.get()/router.post() call (NOT decorator)
    ("Express",
     re.compile(r'(?:^|\s)(?:app|router)\.(get|post|put|delete|patch|all)\s*\(\s*["\']([^"\']+)["\']'),
     {"method": 1, "path": 2}),
    # Django urls.py
    ("Django",
     re.compile(r'(?:path|re_path|url)\s*\(\s*["\']([^"\']*)["\']'),
     {"path": 1}),
    # Go net/http / gin / fiber
    ("Go/Gin",
     re.compile(r'\.\s*(GET|POST|PUT|DELETE|PATCH|Handle)\s*\(\s*"([^"]+)"'),
     {"method": 1, "path": 2}),
]

def route_detection(repo_path: Path, scan_data: dict) -> dict:
    """
    Deep route extraction: method, path, handler file, framework.
    Returns grouped routes and total count.
    """
    routes: list[dict] = []
    seen: set[tuple] = set()

    source_exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rb"}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            p = Path(root) / fname
            rel = str(p.relative_to(repo_path)).replace("\\", "/")

            # File-based routing: Next.js pages/ or app/ directories
            if re.search(r'^(pages|app)/.*\.(jsx?|tsx?)$', rel) and \
               not re.search(r'_(app|document|layout|loading|error)', rel):
                path_part = re.sub(r'\.(jsx?|tsx?)$', '', re.sub(r'^(pages|app)/', '', rel))
                path_part = re.sub(r'/index$', '', f"/{path_part}")
                path_part = re.sub(r'\[([^\]]+)\]', r':\1', path_part)  # [id] -> :id
                key = ("GET", path_part, "FileRouter")
                if key not in seen:
                    seen.add(key)
                    routes.append({"method": "GET", "path": path_part,
                                   "file": rel, "framework": "FileRouter"})
                continue

            if p.suffix not in source_exts:
                continue
            text = _safe_read(p)
            if not text:
                continue

            for framework, pattern, groups in _ROUTE_REGEXES[:-1]:  # skip FileRouter
                for m in pattern.finditer(text):
                    method = (m.group(groups["method"]).upper()
                              if "method" in groups else "ANY")
                    path   = m.group(groups["path"])
                    key    = (method, path, framework)
                    if key not in seen:
                        seen.add(key)
                        routes.append({"method": method, "path": path,
                                       "file": rel, "framework": framework})

    # Group by framework
    by_framework: dict[str, list[dict]] = defaultdict(list)
    by_method:    dict[str, int]         = defaultdict(int)
    for r in routes:
        by_framework[r["framework"]].append(r)
        by_method[r["method"]] += 1

    return {
        "routes": sorted(routes, key=lambda x: (x["file"], x["path"])),
        "total": len(routes),
        "by_framework": {k: v for k, v in sorted(by_framework.items())},
        "by_method": dict(sorted(by_method.items())),
        "frameworks_detected": sorted(by_framework.keys()),
    }


# ─────────────────────────────────────────────────────────────
# 7. DEPENDENCY IMPACT MAP
# ─────────────────────────────────────────────────────────────
def dependency_impact(repo_path: Path, scan_data: dict) -> dict:
    """
    Parse dependency manifests and cross-reference with source code
    to show: dep name, version, how many files use it, which dirs it touches.
    """
    deps: dict[str, str] = {}

    for cfg in scan_data.get("configs", []):
        if "requirements" in cfg and cfg.endswith(".txt"):
            full = repo_path / cfg
            if full.exists():
                for line in full.read_text(errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        m = re.match(r'^([\w\-\.]+)\s*([><=!~^]+.*)?$', line)
                        if m:
                            deps[m.group(1).lower()] = m.group(2) or "*"

    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        import json
        try:
            pkg = json.loads(pkg_json.read_text())
            for section in ("dependencies", "devDependencies"):
                for name, ver in pkg.get(section, {}).items():
                    deps[name.lower()] = ver
        except Exception:
            pass

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(errors="ignore")
        for m in re.finditer(r'"([\w\-]+)\s*([><=!~^][^"]*)"', text):
            deps[m.group(1).lower()] = m.group(2)

    gomod = repo_path / "go.mod"
    if gomod.exists():
        for m in re.finditer(r'require\s+([\w./\-]+)\s+(v[\d.]+)', gomod.read_text()):
            short = m.group(1).split("/")[-1]
            deps[short.lower()] = m.group(2)

    if not deps:
        return {"deps": [], "total": 0, "note": "No dependency manifest found."}

    impact: dict[str, dict] = {name: {"version": ver, "file_count": 0, "dirs": set()}
                                for name, ver in deps.items()}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            p = Path(root) / fname
            if p.suffix not in (".py", ".js", ".ts", ".jsx", ".tsx", ".go"):
                continue
            text = _safe_read(p, max_bytes=50_000)
            if not text:
                continue
            rel = str(p.relative_to(repo_path))
            top_dir = rel.split(os.sep)[0] if os.sep in rel else rel.split("/")[0]

            for name in deps:
                # Fuzzy match: normalise hyphens, match as whole import token
                pattern = re.escape(name.replace("-", "[-_]?"))
                if re.search(rf'\b{pattern}\b', text, re.I):
                    impact[name]["file_count"] += 1
                    impact[name]["dirs"].add(top_dir)

    result_deps = [
        {"name": name,
         "version": info["version"],
         "file_count": info["file_count"],
         "dirs_affected": sorted(info["dirs"]),
         "impact": ("high" if info["file_count"] > 10
                    else "medium" if info["file_count"] > 3
                    else "low")}
        for name, info in impact.items()
        if info["file_count"] > 0
    ]
    result_deps.sort(key=lambda x: -x["file_count"])

    zero_use = [name for name, info in impact.items() if info["file_count"] == 0]

    return {
        "deps": result_deps[:40],
        "total": len(deps),
        "referenced_count": len(result_deps),
        "possibly_unused": zero_use[:20],
        "high_impact": [d for d in result_deps if d["impact"] == "high"],
    }


# ─────────────────────────────────────────────────────────────
# 8. HIDDEN COMPLEXITY DETECTION
# ─────────────────────────────────────────────────────────────
_COMPLEXITY_SIGNALS = {
    "dynamic_dispatch":  (re.compile(r'getattr\(|__getattr__|eval\(|exec\('), "Python dynamic dispatch (getattr/eval/exec) — hard to trace statically"),
    "monkey_patching":   (re.compile(r'monkeypatch|setattr\(.*,\s*["\']'), "Monkey patching detected — runtime behaviour may differ from source"),
    "global_state":      (re.compile(r'^[A-Z_]{3,}\s*=\s*(?!\s*None)[^#\n]+$', re.M), "Global mutable state — shared across modules"),
    "threading":         (re.compile(r'threading\.|Thread\(|asyncio\.|concurrent\.futures'), "Concurrency (threading/asyncio) — race conditions possible"),
    "circular_potential":(re.compile(r'^from\s+\.\s+import|^from\s+\.\.\s+import', re.M), "Relative imports — potential for circular dependencies"),
    "god_file":          (None, "File exceeds 500 lines — may be a god file"),
    "deep_nesting":      (None, "Function with deep nesting (>4 levels) detected"),
    "magic_numbers":     (re.compile(r'\b(?<!\w)(?!0b|0x|0o)[0-9]{3,}\b(?!\s*[=#\]])'), "Magic numbers in logic — undocumented constants"),
    "todo_fixme":        (re.compile(r'#\s*(?:TODO|FIXME|HACK|XXX|BUG)\b', re.I), "TODO/FIXME comments — known debt markers"),
    "subprocess_shell":  (re.compile(r'subprocess\.(?:call|run|Popen).*shell\s*=\s*True'), "subprocess with shell=True — potential command injection"),
    "hardcoded_secrets": (re.compile(r'''(?:password|secret|token|api_key)\s*=\s*['"][^'"]{8,}['"]''', re.I), "Possible hardcoded credentials"),
    "catch_all_except":  (re.compile(r'except\s*:'), "Bare except clauses — swallowing all exceptions"),
    "deep_inheritance":  (re.compile(r'class\s+\w+\(\w+\(\w+'), "Multi-level class inheritance — complex hierarchy"),
}

def hidden_complexity(repo_path: Path, scan_data: dict) -> dict:
    findings: dict[str, list[dict]] = defaultdict(list)
    line_count_total = 0
    large_files = []
    deep_nested_files = []

    py_files = _walk_source(repo_path, {".py"})
    all_source = _walk_source(repo_path, {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rb"})

    for p in all_source:
        text = _safe_read(p)
        if not text:
            continue
        rel = str(p.relative_to(repo_path))
        lines = text.splitlines()
        line_count_total += len(lines)

        if len(lines) > 500:
            large_files.append({"file": rel, "lines": len(lines)})

        for key, (pattern, _) in _COMPLEXITY_SIGNALS.items():
            if pattern is None:
                continue
            matches = list(pattern.finditer(text))
            if matches:
                findings[key].append({
                    "file": rel,
                    "occurrences": len(matches),
                    "first_line": text[:matches[0].start()].count("\n") + 1,
                })

    # Deep nesting measured by max indentation depth (Python only)
    for p in py_files:
        text = _safe_read(p)
        if not text:
            continue
        rel = str(p.relative_to(repo_path))
        max_depth = 0
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                depth = (len(line) - len(stripped)) // 4
                max_depth = max(max_depth, depth)
        if max_depth >= 5:
            deep_nested_files.append({"file": rel, "max_indent_depth": max_depth})

    # Summarise
    summary = []
    for key, (_, description) in _COMPLEXITY_SIGNALS.items():
        if key == "god_file" or key == "deep_nesting":
            continue
        if key in findings:
            total_occ = sum(f["occurrences"] for f in findings[key])
            summary.append({
                "signal": key,
                "description": description,
                "file_count": len(findings[key]),
                "total_occurrences": total_occ,
                "severity": "high" if total_occ > 20 else "medium" if total_occ > 5 else "low",
                "examples": findings[key][:3],
            })

    if large_files:
        summary.append({
            "signal": "god_file",
            "description": _COMPLEXITY_SIGNALS["god_file"][1],
            "file_count": len(large_files),
            "total_occurrences": len(large_files),
            "severity": "medium" if len(large_files) < 5 else "high",
            "examples": sorted(large_files, key=lambda x: -x["lines"])[:5],
        })

    if deep_nested_files:
        summary.append({
            "signal": "deep_nesting",
            "description": _COMPLEXITY_SIGNALS["deep_nesting"][1],
            "file_count": len(deep_nested_files),
            "total_occurrences": len(deep_nested_files),
            "severity": "medium",
            "examples": sorted(deep_nested_files, key=lambda x: -x["max_indent_depth"])[:5],
        })

    summary.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["severity"]])

    return {
        "findings": summary,
        "total_signals": len(summary),
        "high_severity": [s for s in summary if s["severity"] == "high"],
        "source_lines_scanned": line_count_total,
        "large_files": sorted(large_files, key=lambda x: -x["lines"])[:10],
    }


# ─────────────────────────────────────────────────────────────
# 9. ARCHITECTURE CLASSIFICATION
# ─────────────────────────────────────────────────────────────
_ARCH_SIGNALS: dict[str, list] = {
    "monolith": [
        ("dir", re.compile(r'^(models?|views?|controllers?|templates?|static)$', re.I)),
        ("file", re.compile(r'(manage\.py|app\.py|wsgi\.py)')),
        ("content", re.compile(r'(django|flask|rails|laravel)', re.I)),
    ],
    "microservices": [
        ("dir", re.compile(r'^(services?|svc|micro)', re.I)),
        ("file", re.compile(r'docker-compose')),
        ("content", re.compile(r'(grpc|protobuf|service_name|kubernetes|helm)', re.I)),
    ],
    "event_driven": [
        ("content", re.compile(r'(kafka|rabbitmq|celery|pubsub|event_bus|on_message|subscribe\()', re.I)),
        ("dir", re.compile(r'^(events?|handlers?|consumers?|producers?)$', re.I)),
    ],
    "serverless": [
        ("file", re.compile(r'(serverless\.yml|sam\.yaml|template\.yaml|handler\.py|lambda_)')),
        ("content", re.compile(r'(aws_lambda|lambda_handler|@app\.route.*|vercel|netlify)', re.I)),
    ],
    "layered_mvc": [
        ("dir", re.compile(r'^(models?|views?|controllers?|presenters?)$', re.I)),
        ("content", re.compile(r'(Controller|ViewModel|Repository|Service)\b')),
    ],
    "clean_ddd": [
        ("dir", re.compile(r'^(domain|application|infrastructure|adapters?|ports?|usecases?)$', re.I)),
        ("content", re.compile(r'(Repository|UseCase|Aggregate|ValueObject|DomainEvent)\b')),
    ],
    "pipeline": [
        ("dir", re.compile(r'^(pipeline|stages?|steps?|tasks?)$', re.I)),
        ("content", re.compile(r'(Pipeline|Stage|Step|Transform|Processor)\b')),
    ],
    "plugin_based": [
        ("dir", re.compile(r'^(plugins?|extensions?|addons?|middleware)$', re.I)),
        ("content", re.compile(r'(plugin_manager|register_plugin|load_extension|hook\()', re.I)),
    ],
    "fullstack_spa": [
        ("dir", re.compile(r'^(frontend|backend|client|server|api)$', re.I)),
        ("content", re.compile(r'(react|vue|angular|svelte|next|nuxt)', re.I)),
    ],
}

def architecture_classify(repo_path: Path, scan_data: dict) -> dict:
    scores: dict[str, int] = defaultdict(int)
    evidence: dict[str, list[str]] = defaultdict(list)

    dirs_seen  = set()
    files_seen = set()
    content_cache: list[tuple[str, str]] = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for d in dirs:
            dirs_seen.add(d.lower())
        for fname in files:
            p = Path(root) / fname
            files_seen.add(fname.lower())
            if p.suffix in (".py", ".js", ".ts", ".go") and len(content_cache) < 100:
                text = _safe_read(p, max_bytes=20_000)
                if text:
                    content_cache.append((str(p.relative_to(repo_path)), text))

    for arch, signals in _ARCH_SIGNALS.items():
        for sig_type, pattern in signals:
            if sig_type == "dir":
                for d in dirs_seen:
                    if pattern.search(d):
                        scores[arch] += 3
                        evidence[arch].append(f"dir: {d}")
                        break
            elif sig_type == "file":
                for f in files_seen:
                    if pattern.search(f):
                        scores[arch] += 4
                        evidence[arch].append(f"file: {f}")
                        break
            elif sig_type == "content":
                for rel, text in content_cache:
                    m = pattern.search(text)
                    if m:
                        scores[arch] += 2
                        evidence[arch].append(f"code: '{m.group(0)[:40]}' in {rel}")
                        break

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    if not ranked or ranked[0][1] == 0:
        primary = "unknown"
        confidence = "low"
    else:
        primary = ranked[0][0]
        top_score = ranked[0][1]
        second_score = ranked[1][1] if len(ranked) > 1 else 0
        confidence = ("high"   if top_score >= 8 and top_score > second_score * 1.5
                      else "medium" if top_score >= 5
                      else "low")

    labels = {
        "monolith":     "Monolithic Web App",
        "microservices":"Microservices",
        "event_driven": "Event-Driven",
        "serverless":   "Serverless / FaaS",
        "layered_mvc":  "Layered MVC",
        "clean_ddd":    "Clean Architecture / DDD",
        "pipeline":     "Data Pipeline",
        "plugin_based": "Plugin-Based / Extensible",
        "fullstack_spa":"Full-Stack SPA",
        "unknown":      "Unknown",
    }

    descriptions = {
        "monolith":     "Traditional single-deployment web app with shared models, views, and controllers.",
        "microservices":"Multiple independently deployable services communicating over the network.",
        "event_driven": "Components communicate via message queues or event buses (Kafka, RabbitMQ, Celery, etc.).",
        "serverless":   "Functions deployed to serverless infrastructure (AWS Lambda, Vercel, Netlify, etc.).",
        "layered_mvc":  "Classic Model-View-Controller layering with clear separation of concerns.",
        "clean_ddd":    "Domain-driven design with clean architecture layers (domain, application, infrastructure).",
        "pipeline":     "Data transformation pipeline with sequential processing stages.",
        "plugin_based": "Core system extensible via plugins, middleware, or hooks.",
        "fullstack_spa":"Frontend SPA (React/Vue/Angular/Svelte) backed by an API server.",
        "unknown":      "Architecture could not be determined from file structure alone.",
    }

    candidates = [
        {"arch": a, "label": labels.get(a, a), "score": s,
         "evidence": evidence[a][:4]}
        for a, s in ranked if s > 0
    ]

    return {
        "primary":     primary,
        "label":       labels.get(primary, primary),
        "description": descriptions.get(primary, ""),
        "confidence":  confidence,
        "candidates":  candidates[:5],
        "all_scores":  dict(ranked),
    }


# ─────────────────────────────────────────────────────────────
# RUNNER — called from repomap.py
# ─────────────────────────────────────────────────────────────
ANALYZERS = [
    ("first_day_path",         first_day_path),
    ("naming_consistency",     naming_consistency),
    ("entry_point_confidence", entry_point_confidence),
    ("flow_trace",             flow_trace),
    ("parameter_tracking",     parameter_tracking),
    ("route_detection",        route_detection),
    ("dependency_impact",      dependency_impact),
    ("hidden_complexity",      hidden_complexity),
    ("architecture",           architecture_classify),
]

def run_all(repo_path: Path, scan_data: dict,
            selected: Optional[list[str]] = None) -> dict:
    """
    Run all (or a selected subset of) analyzers.
    Results are stored in scan_data["analysis"][name].
    Returns the populated analysis dict.
    """
    analysis = {}
    # Make analysis available mid-run so later analyzers can reference earlier results
    scan_data["analysis"] = analysis

    for name, fn in ANALYZERS:
        if selected and name not in selected:
            continue
        try:
            analysis[name] = fn(repo_path, scan_data)
        except Exception as exc:
            analysis[name] = {"error": str(exc)}

    return analysis
