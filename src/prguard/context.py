from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

from prguard.models import PRSnapshot

STDLIB = set(getattr(sys, "stdlib_module_names", ()))


def python_symbols(path: str, source: str) -> dict[str, list[str]]:
    """Extract stable, bounded symbol/call summaries for Python context prompts."""
    if not path.endswith(".py"):
        return {"functions": [], "classes": [], "calls": []}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {"functions": [], "classes": [], "calls": []}
    functions = [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))][:80]
    classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)][:40]
    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name not in calls:
            calls.append(name)
        if len(calls) >= 80:
            break
    return {"functions": functions, "classes": classes, "calls": calls}


def python_imports(path: str, source: str) -> set[str]:
    if not path.endswith(".py"):
        return set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return {name for name in modules if name.split(".", 1)[0] not in STDLIB}


def related_python_paths(changed_paths: list[str], source_map: dict[str, str], tree_paths: list[str]) -> list[str]:
    available = set(tree_paths)
    candidates: dict[str, int] = {}
    for changed in changed_paths:
        imports = python_imports(changed, source_map.get(changed, ""))
        module_parts = {name.replace(".", "/") for name in imports}
        for candidate in available:
            if not candidate.endswith(".py") or candidate in changed_paths:
                continue
            stem = candidate[:-3]
            score = 0
            if any(stem.endswith("/" + module) or stem == module for module in module_parts):
                score += 5
            changed_stem = PurePosixPath(changed).stem
            if PurePosixPath(stem).stem == changed_stem or PurePosixPath(stem).stem == f"test_{changed_stem}":
                score += 2
            if "test" in PurePosixPath(candidate).parts and changed_stem in PurePosixPath(candidate).name:
                score += 3
            if score:
                candidates[candidate] = max(score, candidates.get(candidate, 0))
    return [path for path, _ in sorted(candidates.items(), key=lambda item: (-item[1], item[0]))]


def approximate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0


@dataclass(frozen=True)
class ContextBundle:
    full_context: str
    optimized_context: str
    selected_context_paths: tuple[str, ...]
    full_tokens: int
    optimized_tokens: int


def build_context_bundle(
    snapshot: PRSnapshot,
    source_map: dict[str, str],
    related_sources: dict[str, str],
    *,
    top_k: int = 5,
    per_file_chars: int = 12_000,
) -> ContextBundle:
    """Rank whole context files; changed Diff hunks are always kept intact."""
    changed = "\n".join(f"{path}\n{item.patch or ''}" for path, item in ((f.path, f) for f in snapshot.files))
    seen_hashes: set[str] = set()
    full_blocks = [f"Changed PR diff (complete={snapshot.diff_complete}):\n{snapshot.diff}"]
    ranked: list[tuple[int, str, str]] = []
    changed_names = {PurePosixPath(path).stem for path in source_map}
    all_sources = {**source_map, **related_sources}
    for path, source in all_sources.items():
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        symbols = python_symbols(path, source)
        summary = ""
        if path.endswith(".py"):
            summary = (
                f"\nPython symbols: functions={', '.join(symbols['functions']) or '-'}; "
                f"classes={', '.join(symbols['classes']) or '-'}; calls={', '.join(symbols['calls']) or '-'}"
            )
        full_blocks.append(f"\nContext file: {path}{summary}\n{source}")
        stem = PurePosixPath(path).stem
        score = 3 if stem.startswith("test_") and stem[5:] in changed_names else 0
        score += 2 if stem in changed_names else 0
        score += len(set(source.split()) & set(changed.split())) // 50
        if source and path not in source_map:
            ranked.append((score, path, source))

    selected = sorted(ranked, key=lambda row: (-row[0], row[1]))[:top_k]
    allowed_paths = ", ".join(file.path for file in snapshot.files) or "(none)"
    allowed_line = (
        "Changed files (a finding's file_path must be exactly one of these, copied verbatim; "
        f"never 'unknown', an empty string or a bare filename): {allowed_paths}"
    )
    optimized_blocks = [
        f"{allowed_line}\n\nChanged PR diff (complete={snapshot.diff_complete}):\n{snapshot.diff}"
    ]
    selected_paths: list[str] = []
    used_chars = len(optimized_blocks[0])
    for _, path, source in selected:
        if len(source) > per_file_chars or used_chars + len(source) > per_file_chars * max(top_k, 1):
            continue
        symbols = python_symbols(path, source)
        summary = (
            f"\nPython symbols: functions={', '.join(symbols['functions']) or '-'}; "
            f"classes={', '.join(symbols['classes']) or '-'}; calls={', '.join(symbols['calls']) or '-'}"
            if path.endswith(".py") else ""
        )
        optimized_blocks.append(f"\nContext file: {path}{summary}\n{source}")
        selected_paths.append(path)
        used_chars += len(source)

    full_context = "\n".join(full_blocks)
    optimized_context = "\n".join(optimized_blocks)
    return ContextBundle(
        full_context=full_context,
        optimized_context=optimized_context,
        selected_context_paths=tuple(selected_paths),
        full_tokens=approximate_tokens(full_context),
        optimized_tokens=approximate_tokens(optimized_context),
    )
