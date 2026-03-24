"""
symbol_graph.py — repomap cross-file symbol map.

Finds every function, class, and variable that is DEFINED in one file
and USED (imported or called/referenced) in one or more OTHER files.

Outputs
───────
  • terminal summary        — top symbols by cross-file reach
  • <slug>_symbols.json     — full machine-readable graph
  • <slug>_symbols.md       — human-readable symbol reference doc

Supported languages
───────────────────
  Python  — AST-based (exact): functions, classes, module-level variables,
             __all__ exports, dataclasses, TypeVar, Protocol
  JS/TS   — regex-based (good): exported functions, classes, consts/lets,
             interface/type/enum, re-export patterns
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Symbol:
    name:      str
    kind:      str        # "function" | "class" | "variable" | "type" | "constant"
    defined_in: str       # relative file path (forward slashes)
    line:      int        # definition line number
    exported:  bool       # explicitly exported / in __all__
    signature: str = ""   # first line of def / type hint / rhs snippet

@dataclass
class CrossRef:
    symbol:      str
    kind:        str
    defined_in:  str
    used_in:     list[str] = field(default_factory=list)   # files that import/use it
    use_count:   int = 0                                    # total reference count
    call_sites:  list[dict] = field(default_factory=list)  # {file, line, context}

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".env", "dist", "build", ".next", ".nuxt", "target", "vendor",
    ".tox", "eggs", ".eggs",
}

SOURCE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx"}

ANSI = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "cyan":    "\033[36m",
    "red":     "\033[31m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
}

def _c(text, *keys):
    return "".join(ANSI[k] for k in keys) + str(text) + ANSI["reset"]


def _walk_source(repo_path: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.endswith(".egg-info")]
        for f in files:
            p = Path(root) / f
            if p.suffix in SOURCE_EXTS:
                out.append(p)
    return out

def _rel(repo_path: Path, p: Path) -> str:
    return p.relative_to(repo_path).as_posix()

def _safe_read(p: Path, max_bytes: int = 300_000) -> Optional[str]:
    try:
        if p.stat().st_size > max_bytes:
            return None
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def _py_extract_definitions(rel: str, text: str) -> list[Symbol]:
    symbols: list[Symbol] = []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return symbols

    all_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                all_names.add(elt.value)

    lines = text.splitlines()

    def sig(node) -> str:
        """Grab the definition line as a compact signature."""
        try:
            ln = lines[node.lineno - 1].strip()
            return ln[:120]
        except (IndexError, AttributeError):
            return ""

    # Only walk top-level and class-level nodes
    for node in ast.iter_child_nodes(tree):

        # Functions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):   # skip private
                symbols.append(Symbol(
                    name=node.name, kind="function",
                    defined_in=rel, line=node.lineno,
                    exported=node.name in all_names or bool(all_names) is False,
                    signature=sig(node),
                ))

        # Classes (and their public methods)
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                symbols.append(Symbol(
                    name=node.name, kind="class",
                    defined_in=rel, line=node.lineno,
                    exported=node.name in all_names or bool(all_names) is False,
                    signature=sig(node),
                ))
                # Public methods
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        mname = child.name
                        if not mname.startswith("_") or mname in ("__init__", "__call__"):
                            symbols.append(Symbol(
                                name=f"{node.name}.{mname}",
                                kind="method",
                                defined_in=rel, line=child.lineno,
                                exported=False,
                                signature=sig(child),
                            ))

        # Module-level assignments (variables / constants)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    # Skip trivial assignments to keep noise low
                    rhs = ast.unparse(node.value)[:60] if hasattr(ast, "unparse") else ""
                    symbols.append(Symbol(
                        name=t.id, kind="constant" if t.id.isupper() else "variable",
                        defined_in=rel, line=node.lineno,
                        exported=t.id in all_names or bool(all_names) is False,
                        signature=f"{t.id} = {rhs}",
                    ))

        # Annotated assignments: x: int = 5
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                ann = ast.unparse(node.annotation)[:40] if hasattr(ast, "unparse") else ""
                symbols.append(Symbol(
                    name=node.target.id,
                    kind="constant" if node.target.id.isupper() else "variable",
                    defined_in=rel, line=node.lineno,
                    exported=node.target.id in all_names or bool(all_names) is False,
                    signature=f"{node.target.id}: {ann}",
                ))

    return symbols


def _py_find_usages(rel: str, text: str, symbol_names: set[str]) -> list[tuple[str, int, str]]:
    """
    Return (symbol_name, line_number, context_snippet) for each reference to a
    known cross-file symbol that was imported into this file from another module.
    """
    results = []
    lines = text.splitlines()

    # ── Step 1: collect names imported into this file ──────────
    # imported_map: local_name -> original_symbol_name
    imported_map: dict[str, str] = {}
    try:
        tree = ast.parse(text, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    orig  = alias.name          # the symbol being imported
                    local = alias.asname or alias.name
                    # Direct symbol import: from x import get_db
                    if orig in symbol_names:
                        imported_map[local] = orig
                    # Module import: from app.routes import users  →  users.router may be used
                    if local not in imported_map:
                        imported_map[local] = local   # track module names too
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    if local in symbol_names:
                        imported_map[local] = local
    except SyntaxError:
        pass

    if not imported_map:
        return results

    # ── Step 2: scan every non-import line for usages ──────────
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        is_import_line = stripped.startswith(("import ", "from "))

        for local_name, orig_name in imported_map.items():
            if not re.search(rf'\b{re.escape(local_name)}\b', stripped):
                continue
            # Use the original symbol name for cross-referencing
            sym_name = orig_name if orig_name in symbol_names else local_name
            if sym_name not in symbol_names:
                continue
            # Record usage; import lines count as "imported here" but we skip
            # them as call sites (they're not actual usage sites)
            if not is_import_line:
                results.append((sym_name, lineno, stripped[:100]))

    return results


_JS_EXPORT_PATTERNS = [
    # export function foo / export async function foo
    (re.compile(r'^export\s+(?:async\s+)?function\s+(\w+)'), "function"),
    # export const/let/var foo
    (re.compile(r'^export\s+(?:const|let|var)\s+(\w+)'), "constant"),
    # export class Foo
    (re.compile(r'^export\s+class\s+(\w+)'), "class"),
    # export type Foo / export interface Foo
    (re.compile(r'^export\s+(?:type|interface)\s+(\w+)'), "type"),
    # export enum Foo
    (re.compile(r'^export\s+enum\s+(\w+)'), "type"),
    # export default function foo / export default class Foo
    (re.compile(r'^export\s+default\s+(?:async\s+)?(?:function|class)\s+(\w+)'), "function"),
    # const foo = ... ; export { foo }   →  handled separately below
]

_JS_REEXPORT = re.compile(r'^export\s*\{([^}]+)\}')  # export { a, b as c }

def _js_extract_definitions(rel: str, text: str) -> list[Symbol]:
    symbols: list[Symbol] = []
    lines = text.splitlines()

    reexported: set[str] = set()
    for line in lines:
        m = _JS_REEXPORT.match(line.strip())
        if m:
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                if name:
                    reexported.add(name)

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        for pattern, kind in _JS_EXPORT_PATTERNS:
            m = pattern.match(stripped)
            if m:
                name = m.group(1)
                symbols.append(Symbol(
                    name=name, kind=kind,
                    defined_in=rel, line=lineno,
                    exported=True,
                    signature=stripped[:120],
                ))
                break

    return symbols


def _js_find_usages(rel: str, text: str, symbol_names: set[str]) -> list[tuple[str, int, str]]:
    results = []
    lines = text.splitlines()

    imported_here: set[str] = set()
    import_pat = re.compile(
        r'''import\s*\{([^}]+)\}\s*from|import\s+(\w+)\s+from|import\s+\*\s+as\s+(\w+)\s+from'''
    )
    for line in lines:
        for m in import_pat.finditer(line):
            group = m.group(1) or m.group(2) or m.group(3) or ""
            for part in group.split(","):
                name = part.strip().split(" as ")[-1].strip()
                if name in symbol_names:
                    imported_here.add(name)

    if not imported_here:
        return results

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("import ", "//", "/*", " *")):
            continue
        for name in imported_here:
            if re.search(rf'\b{re.escape(name)}\b', stripped):
                results.append((name, lineno, stripped[:100]))
    return results


def build_symbol_graph(repo_path: Path) -> dict:
    """
    Two-pass analysis:
      Pass 1 — extract all symbol definitions from every source file
      Pass 2 — find usages of cross-file symbols in every other source file

    Returns a rich dict ready for JSON serialisation and report writing.
    """
    files = _walk_source(repo_path)

    # ── Pass 1: definitions ───────────────────────────────────
    # symbol_name -> list[Symbol]  (same name may be defined in many files)
    definitions: dict[str, list[Symbol]] = defaultdict(list)

    for p in files:
        rel  = _rel(repo_path, p)
        text = _safe_read(p)
        if not text:
            continue

        if p.suffix == ".py":
            syms = _py_extract_definitions(rel, text)
        elif p.suffix in (".js", ".ts", ".jsx", ".tsx"):
            syms = _js_extract_definitions(rel, text)
        else:
            continue

        for sym in syms:
            definitions[sym.name].append(sym)

    # Only care about symbols defined in exactly one file (unambiguous origin)
    # AND whose name appears in at least one other file's import statements
    unambiguous: dict[str, Symbol] = {}
    for name, syms in definitions.items():
        # Prefer: classes > functions > variables; exported > not
        syms_sorted = sorted(syms, key=lambda s: (
            {"class": 0, "function": 1, "method": 2,
             "constant": 3, "variable": 4, "type": 5}.get(s.kind, 9),
            not s.exported,
        ))
        unambiguous[name] = syms_sorted[0]

    all_names = set(unambiguous.keys())

    # ── Pass 2: usages ────────────────────────────────────────
    cross_refs: dict[str, CrossRef] = {}

    for p in files:
        rel  = _rel(repo_path, p)
        text = _safe_read(p)
        if not text:
            continue

        if p.suffix == ".py":
            # Also do a first-pass: which symbols are explicitly imported here?
            imported_syms: set[str] = set()
            try:
                tree = ast.parse(text, filename=rel)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            orig = alias.name
                            if orig in unambiguous and unambiguous[orig].defined_in != rel:
                                imported_syms.add(orig)
                            local = alias.asname or alias.name
                            if local in unambiguous and unambiguous[local].defined_in != rel:
                                imported_syms.add(local)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            name = alias.asname or alias.name.split(".")[0]
                            if name in unambiguous and unambiguous[name].defined_in != rel:
                                imported_syms.add(name)
            except SyntaxError:
                pass

            # Register each imported symbol as used_in this file
            for sym_name in imported_syms:
                sym = unambiguous[sym_name]
                if sym_name not in cross_refs:
                    cross_refs[sym_name] = CrossRef(
                        symbol=sym_name, kind=sym.kind,
                        defined_in=sym.defined_in,
                    )
                cr = cross_refs[sym_name]
                if rel not in cr.used_in:
                    cr.used_in.append(rel)

            usages = _py_find_usages(rel, text, all_names)

        elif p.suffix in (".js", ".ts", ".jsx", ".tsx"):
            usages = _js_find_usages(rel, text, all_names)
        else:
            continue

        for sym_name, lineno, context in usages:
            sym = unambiguous.get(sym_name)
            if sym is None:
                continue
            if sym.defined_in == rel:
                continue

            if sym_name not in cross_refs:
                cross_refs[sym_name] = CrossRef(
                    symbol=sym_name, kind=sym.kind,
                    defined_in=sym.defined_in,
                )
            cr = cross_refs[sym_name]
            if rel not in cr.used_in:
                cr.used_in.append(rel)
            cr.use_count += 1
            if len(cr.call_sites) < 5:
                cr.call_sites.append({"file": rel, "line": lineno, "context": context})

    # ── Enrich with definition signature ─────────────────────
    result_symbols = []
    for name, cr in sorted(cross_refs.items(),
                            key=lambda x: -len(x[1].used_in)):
        sym = unambiguous[name]
        result_symbols.append({
            "name":       name,
            "kind":       cr.kind,
            "defined_in": cr.defined_in,
            "line":       sym.line,
            "signature":  sym.signature,
            "exported":   sym.exported,
            "used_in":    sorted(cr.used_in),
            "file_count": len(cr.used_in),
            "use_count":  cr.use_count,
            "call_sites": cr.call_sites,
        })

    # ── Aggregate stats ───────────────────────────────────────
    by_kind: dict[str, int] = defaultdict(int)
    by_file: dict[str, int] = defaultdict(int)    # file -> how many symbols it exports cross-file
    for s in result_symbols:
        by_kind[s["kind"]] += 1
        by_file[s["defined_in"]] += 1

    top_exporters = sorted(by_file.items(), key=lambda x: -x[1])[:20]

    by_defining_file: dict[str, list[dict]] = defaultdict(list)
    for s in result_symbols:
        by_defining_file[s["defined_in"]].append(s)

    return {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "repo_path":       str(repo_path),
        "total_symbols":   len(result_symbols),
        "total_files":     len(files),
        "by_kind":         dict(sorted(by_kind.items())),
        "top_exporters":   [{"file": f, "symbol_count": n} for f, n in top_exporters],
        "symbols":         result_symbols,
        "by_file":         {k: v for k, v in sorted(by_defining_file.items())},
    }


def print_symbol_summary(graph: dict):
    total  = graph["total_symbols"]
    by_kind = graph["by_kind"]

    print()
    print(_c("  ┌─────────────────────────────────────────────────────┐", "dim"))
    print(_c("  │", "dim") + _c("  repomap", "bold", "cyan") +
          _c(" — cross-file symbol map", "dim") +
          _c("                    │", "dim"))
    print(_c("  └─────────────────────────────────────────────────────┘", "dim"))

    print(_c(f"\n  ◆ Summary", "bold", "cyan"))
    print(f"    {_c('Total cross-file symbols:', 'dim')}  {_c(str(total), 'bold')}")
    for kind, count in sorted(by_kind.items()):
        bar = "█" * min(count, 30)
        print(f"    {_c(kind.ljust(12), 'dim')}  {_c(bar, 'cyan')}  {count}")

    print(_c(f"\n  ◆ Top exporting files", "bold", "cyan"))
    for entry in graph["top_exporters"][:10]:
        n = entry["symbol_count"]
        print(f"    {_c(str(n).rjust(4), 'yellow')}  {entry['file']}")

    print(_c(f"\n  ◆ Most-used symbols  (by number of files that import them)", "bold", "cyan"))
    for sym in graph["symbols"][:20]:
        reach   = sym["file_count"]
        kind_col = (
            "green"   if sym["kind"] == "function" else
            "cyan"    if sym["kind"] == "class"    else
            "yellow"  if sym["kind"] in ("constant", "variable") else
            "magenta"
        )
        print(
            f"    {_c(sym['kind'].ljust(10), kind_col)}"
            f"  {_c(sym['name'].ljust(35), 'bold')}"
            f"  {_c(str(reach)+' files', 'yellow')}"
            f"  ← {_c(sym['defined_in'], 'dim')}"
        )

    print()


def write_symbol_json(graph: dict, output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2, ensure_ascii=False)
    print(_c(f"  ✓ Symbol JSON  → {output_path}", "green"))


KIND_EMOJI = {
    "function": "🔧",
    "method":   "🔩",
    "class":    "🏛️",
    "constant": "📌",
    "variable": "📦",
    "type":     "🔷",
}

def write_symbol_markdown(graph: dict, repo_url: str, output_path: str):
    lines: list[str] = []
    total  = graph["total_symbols"]
    by_kind = graph["by_kind"]

    lines += [
        "# Cross-File Symbol Map",
        "",
        f"> Generated by **repomap** on {graph['generated_at']}  ",
        f"> Repository: {repo_url}",
        "",
        "This document lists every function, class, variable, and type that is **defined in one file "
        "and imported or used in at least one other file**. It shows you the public API surface "
        "of each module and where each symbol travels across the codebase.",
        "",
        "---",
        "",
    ]

    lines += ["## Overview", ""]
    lines += [f"| Metric | Value |", f"|--------|-------|"]
    lines += [f"| Total cross-file symbols | **{total}** |"]
    lines += [f"| Source files scanned | {graph['total_files']} |"]
    for kind, count in sorted(by_kind.items()):
        emoji = KIND_EMOJI.get(kind, "•")
        lines += [f"| {emoji} {kind.capitalize()}s | {count} |"]
    lines += [""]

    lines += ["## Top Exporting Files", "",
              "Files that contribute the most symbols used elsewhere in the codebase.", "",
              "| File | Symbols exported cross-file |",
              "|------|-----------------------------|"]
    for entry in graph["top_exporters"]:
        lines.append(f"| `{entry['file']}` | {entry['symbol_count']} |")
    lines += [""]

    lines += [
        "## Symbol Index  _(sorted by cross-file reach)_",
        "",
        "| Symbol | Kind | Defined in | Used in (files) | Use count |",
        "|--------|------|-----------|-----------------|-----------|",
    ]
    for sym in graph["symbols"][:50]:
        emoji = KIND_EMOJI.get(sym["kind"], "•")
        anchor = sym["name"].lower().replace(".", "").replace("_", "-")
        files_str = str(sym["file_count"])
        lines.append(
            f"| [{emoji} `{sym['name']}`](#{anchor}) "
            f"| {sym['kind']} "
            f"| `{sym['defined_in']}` "
            f"| {files_str} "
            f"| {sym['use_count']} |"
        )
    if total > 50:
        lines.append(f"\n_… and {total - 50} more symbols in the full JSON report._")
    lines += [""]

    lines += ["---", "", "## By File", "",
              "Each section shows the cross-file symbols a file defines, "
              "the files that consume them, and specific call sites.", ""]

    for defining_file, syms in graph["by_file"].items():
        lines += [f"### `{defining_file}`", ""]
        lines.append(f"Exports **{len(syms)}** symbol(s) used across the codebase.", )
        lines += [""]

        for sym in sorted(syms, key=lambda s: (-s["file_count"], s["name"])):
            emoji = KIND_EMOJI.get(sym["kind"], "•")
            anchor = sym["name"].lower().replace(".", "").replace("_", "-")
            lines += [
                f'<a name="{anchor}"></a>',
                f"#### {emoji} `{sym['name']}` _{sym['kind']}_",
                "",
            ]
            if sym.get("signature"):
                lines += [f"```", sym["signature"], "```", ""]

            lines += [
                f"- **Defined at:** `{sym['defined_in']}` line {sym['line']}",
                f"- **Used in {sym['file_count']} file(s):** "
                + ", ".join(f"`{f}`" for f in sym["used_in"][:8])
                + (f" _+{len(sym['used_in'])-8} more_" if len(sym["used_in"]) > 8 else ""),
                f"- **Total references:** {sym['use_count']}",
                "",
            ]

            if sym.get("call_sites"):
                lines.append("<details>")
                lines.append("<summary>Call sites</summary>")
                lines.append("")
                lines += ["| File | Line | Context |", "|------|------|---------|"]
                for cs in sym["call_sites"]:
                    ctx = cs["context"].replace("|", "\\|")
                    lines.append(f"| `{cs['file']}` | {cs['line']} | `{ctx}` |")
                lines += ["", "</details>", ""]

        lines.append("---")
        lines.append("")

    lines += [
        "_Generated by [repomap](https://github.com/) — cross-file symbol map._",
    ]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(_c(f"  ✓ Symbol Markdown → {output_path}", "green"))


if __name__ == "__main__":
    import sys, tempfile
    if len(sys.argv) < 2:
        print("Usage: python symbol_graph.py <repo_path> [output_dir]")
        sys.exit(1)
    repo = Path(sys.argv[1])
    out  = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    out.mkdir(parents=True, exist_ok=True)

    print(_c(f"\n  ◆ Building symbol graph for {repo}…", "bold", "cyan"))
    graph = build_symbol_graph(repo)
    print_symbol_summary(graph)
    write_symbol_json(graph,     str(out / "symbols.json"))
    write_symbol_markdown(graph, str(repo), str(out / "symbols.md"))
