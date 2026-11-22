#!/usr/bin/env python3
"""Deterministic CycloneDX 1.5 Software Bill of Materials (SBOM) generator.

    python3 tools/sbom.py --out dist/sbom.cdx.json [--version X.Y.Z]

Standard library only (Python 3.9+). Produces a byte-for-byte repeatable CycloneDX 1.5 JSON
document enumerating:

1. Root application component (``learn-my-voice``) with version, MIT license, and repository URL.
2. Python runtime component (``python-stdlib``), verified by AST-scanning every ``import`` and
   ``from ... import`` across ``studio/*.py`` and ``tools/*.py`` to prove zero third-party PyPI
   imports in the application and release tooling.
3. Exact Rust crate dependencies parsed from ``desktop/src-tauri/Cargo.lock`` with ``pkg:cargo/...``
   Package URLs and SHA-256 crate checksums.
4. Pinned GitHub Actions parsed from ``.github/workflows/*.yml`` (``pkg:github/<owner>/<repo>@<sha>``).
5. Bundled first-party prompt blueprints and lexicon/schema data files with SHA-256 digests.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import sysconfig
import uuid
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/LetsLearntocodeforfun/Learn-My-Voice"
SBOM_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL

_CHANGELOG_VERSION = re.compile(r"^## v([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?)\b", re.M)
_USES_RE = re.compile(r"^\s*(?:- )?uses:\s*(\S+)", re.M)
_ACTION_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})$")

# Isolated optional macOS transcription worker: deferred function-level imports that run only inside
# the sandboxed ``local_tools/whisper/venv`` subprocess, never in the studio server, CLI, or tools.
ALLOWED_ISOLATED_WORKER_IMPORTS: Dict[str, FrozenSet[str]] = {
    "studio/whisper_worker.py": frozenset({"mlx_whisper", "numpy"}),
}

# Cross-platform Python 3.9+ standard library top-level module names (covers POSIX-only and Windows-only
# stdlib modules even when running on Python 3.9 before ``sys.stdlib_module_names`` was added in 3.10).
_PY39_CROSS_PLATFORM_STDLIB: FrozenSet[str] = frozenset({
    "__future__", "_abc", "_ast", "_bisect", "_blake2", "_bootlocale", "_bz2", "_codecs",
    "_collections", "_collections_abc", "_compat_pickle", "_compression", "_contextvars",
    "_crypt", "_csv", "_ctypes", "_curses", "_curses_panel", "_datetime", "_dbm", "_decimal",
    "_elementtree", "_functools", "_gdbm", "_hashlib", "_heapq", "_imp", "_io", "_json",
    "_locale", "_lsprof", "_lzma", "_markupbase", "_md5", "_msi", "_multibytecodec",
    "_multiprocessing", "_opcode", "_operator", "_osx_support", "_overlapped", "_pickle",
    "_posixshmem", "_posixsubprocess", "_py_abc", "_pydecimal", "_pyio", "_queue", "_random",
    "_scproxy", "_sha1", "_sha256", "_sha3", "_sha512", "_signal", "_sitebuiltins", "_socket",
    "_sqlite3", "_sre", "_ssl", "_stat", "_statistics", "_string", "_strptime", "_struct",
    "_symtable", "_thread", "_threading_local", "_tkinter", "_tracemalloc", "_uuid", "_warnings",
    "_weakref", "_weakrefset", "_winapi", "abc", "aifc", "antigravity", "argparse", "array",
    "ast", "asynchat", "asyncio", "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "cProfile", "calendar", "cgi", "cgitb", "chunk",
    "cmath", "cmd", "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy", "copyreg", "crypt",
    "csv", "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis",
    "distutils", "doctest", "email", "encodings", "ensurepip", "enum", "errno", "faulthandler",
    "fcntl", "filecmp", "fileinput", "fnmatch", "formatter", "fractions", "ftplib", "functools",
    "gc", "genericpath", "getopt", "getpass", "gettext", "glob", "graphlib", "grp", "gzip",
    "hashlib", "heapq", "hmac", "html", "http", "imaplib", "imghdr", "imp", "importlib",
    "inspect", "io", "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
    "locale", "logging", "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "msilib", "msvcrt", "multiprocessing", "netrc", "nis", "nntplib", "ntpath",
    "nturl2path", "numbers", "opcode", "operator", "optparse", "os", "ossaudiodev", "parser",
    "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib",
    "poplib", "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd", "py_compile",
    "pyclbr", "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "selectors", "shelve", "shlex",
    "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr", "socket", "socketserver",
    "spwd", "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl", "stat", "statistics",
    "string", "stringprep", "struct", "subprocess", "sunau", "symbol", "symtable", "sys",
    "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "this", "threading", "time", "timeit", "tkinter", "token", "tokenize", "tomllib",
    "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing",
    "unicodedata", "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp",
    "zipfile", "zipimport", "zlib", "zoneinfo",
})


def stdlib_module_set() -> Set[str]:
    """Known Python standard-library top-level module names across Python 3.9+ and platforms."""
    names: Set[str] = set(_PY39_CROSS_PLATFORM_STDLIB)
    names.update(sys.builtin_module_names)
    runtime_stdlib = getattr(sys, "stdlib_module_names", None)
    if runtime_stdlib is not None:
        names.update(runtime_stdlib)
    stdlib_path = sysconfig.get_path("stdlib")
    if stdlib_path:
        std_dir = Path(stdlib_path)
        if std_dir.is_dir():
            for child in std_dir.iterdir():
                if child.name == "site-packages":
                    continue
                if child.suffix == ".py":
                    names.add(child.stem)
                elif child.is_dir() and (child / "__init__.py").is_file():
                    names.add(child.name)
            dynload = std_dir / "lib-dynload"
            if dynload.is_dir():
                for child in dynload.iterdir():
                    names.add(child.name.split(".", 1)[0])
    return names


def resolve_version(root: Path = ROOT, explicit_version: Optional[str] = None) -> str:
    """Determine the application version from ``--version``, ``studio/build.json``, ``studio/__init__.py``, or ``CHANGELOG.md``."""
    if explicit_version is not None and explicit_version.strip():
        cleaned = explicit_version.strip()
        if cleaned.startswith("v") and len(cleaned) > 1 and cleaned[1].isdigit():
            cleaned = cleaned[1:]
        return cleaned

    build_json = root / "studio" / "build.json"
    if build_json.is_file():
        try:
            data = json.loads(build_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("version"), str) and data["version"].strip():
                return data["version"].strip()
        except (OSError, ValueError):
            pass

    init_py = root / "studio" / "__init__.py"
    if init_py.is_file():
        tree = ast.parse(init_py.read_text(encoding="utf-8"), filename=str(init_py))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__version__":
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            return node.value.value.strip()

    changelog = root / "CHANGELOG.md"
    if changelog.is_file():
        found = _CHANGELOG_VERSION.search(changelog.read_text(encoding="utf-8"))
        if found:
            return found.group(1)

    return "0.0.0-unknown"


def scan_python_imports(root: Path = ROOT) -> Dict[str, Any]:
    """AST-scan every ``studio/*.py`` and ``tools/*.py`` file to verify stdlib-only compliance."""
    stdlib = stdlib_module_set()
    studio_dir = root / "studio"
    tools_dir = root / "tools"
    local_modules: Set[str] = {"studio", "tools"}
    if studio_dir.is_dir():
        local_modules.update(p.stem for p in studio_dir.glob("*.py"))
    if tools_dir.is_dir():
        local_modules.update(p.stem for p in tools_dir.glob("*.py"))

    py_files: List[Path] = []
    for folder in (studio_dir, tools_dir):
        if folder.is_dir():
            py_files.extend(sorted(folder.glob("*.py")))

    stdlib_used: Set[str] = set()
    isolated_worker_imports: List[Dict[str, Any]] = []
    unexpected_third_party: List[Dict[str, Any]] = []

    for path in py_files:
        rel = path.relative_to(root).as_posix()
        allowed_deferred = ALLOWED_ISOLATED_WORKER_IMPORTS.get(rel, frozenset())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)

        top_level_nodes = set(tree.body)
        for node in ast.walk(tree):
            is_top_level = node in top_level_nodes
            imported: List[Tuple[str, int]] = []
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append((alias.name.split(".", 1)[0], node.lineno))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    imported.append((node.module.split(".", 1)[0], node.lineno))

            for mod_name, lineno in imported:
                if mod_name in local_modules:
                    continue
                if mod_name in stdlib:
                    stdlib_used.add(mod_name)
                    continue
                if (not is_top_level) and mod_name in allowed_deferred:
                    isolated_worker_imports.append({"file": rel, "line": lineno, "module": mod_name})
                else:
                    unexpected_third_party.append({
                        "file": rel,
                        "line": lineno,
                        "module": mod_name,
                        "top_level": is_top_level,
                    })

    isolated_worker_imports.sort(key=lambda item: (item["file"], item["line"], item["module"]))
    unexpected_third_party.sort(key=lambda item: (item["file"], item["line"], item["module"]))
    return {
        "scanned_files": len(py_files),
        "stdlib_modules_used": sorted(stdlib_used),
        "isolated_worker_imports": isolated_worker_imports,
        "unexpected_third_party": unexpected_third_party,
    }


def parse_cargo_lock(cargo_lock_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Deterministically parse ``Cargo.lock`` v3/v4 into sorted external crate entries and direct root deps."""
    if not cargo_lock_path.is_file():
        return [], []

    raw = cargo_lock_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = "[[package]]\n"
    if marker not in raw:
        return [], []
    first_idx = raw.index(marker)
    blocks = raw[first_idx + len(marker):].split("\n[[package]]\n")

    packages: List[Dict[str, Any]] = []
    for block in blocks:
        name = version = source = checksum = None
        deps: List[str] = []
        in_deps = False
        for line in block.splitlines():
            stripped = line.strip()
            if in_deps:
                if stripped == "]":
                    in_deps = False
                elif stripped.startswith('"') and stripped.endswith(('"', '",')):
                    dep_token = stripped.strip('", ')
                    if dep_token:
                        deps.append(dep_token)
                continue
            if stripped.startswith("name = "):
                name = stripped.split('"', 2)[1]
            elif stripped.startswith("version = "):
                version = stripped.split('"', 2)[1]
            elif stripped.startswith("source = "):
                source = stripped.split('"', 2)[1]
            elif stripped.startswith("checksum = "):
                checksum = stripped.split('"', 2)[1]
            elif stripped == "dependencies = [":
                in_deps = True
        if name and version:
            packages.append({
                "name": name,
                "version": version,
                "source": source,
                "checksum": checksum,
                "raw_deps": deps,
            })

    # Map crate name -> versions so Cargo.lock dependency strings ("foo" or "foo 0.2.0") resolve to purls.
    by_name: Dict[str, List[str]] = {}
    for pkg in packages:
        if pkg["source"] is not None:
            by_name.setdefault(pkg["name"], []).append(pkg["version"])
    for ver_list in by_name.values():
        ver_list.sort()

    def resolve_dep_ref(dep_str: str) -> List[str]:
        parts = dep_str.split()
        dep_name = parts[0]
        if len(parts) >= 2 and re.match(r"^[0-9]", parts[1]):
            return ["pkg:cargo/%s@%s" % (dep_name, parts[1])]
        versions = by_name.get(dep_name, [])
        return ["pkg:cargo/%s@%s" % (dep_name, v) for v in versions]

    external_crates: List[Dict[str, Any]] = []
    root_dep_refs: Set[str] = set()

    for pkg in packages:
        resolved_deps: Set[str] = set()
        for d in pkg["raw_deps"]:
            resolved_deps.update(resolve_dep_ref(d))
        if pkg["source"] is None and pkg["name"] == "learn-my-voice":
            root_dep_refs.update(resolved_deps)
            continue
        purl = "pkg:cargo/%s@%s" % (pkg["name"], pkg["version"])
        external_crates.append({
            "name": pkg["name"],
            "version": pkg["version"],
            "purl": purl,
            "source": pkg["source"],
            "checksum": pkg["checksum"],
            "depends_on": sorted(resolved_deps),
        })

    external_crates.sort(key=lambda c: (c["name"], c["version"]))
    return external_crates, sorted(root_dep_refs)


def parse_workflow_actions(workflows_dir: Path) -> List[Dict[str, Any]]:
    """Parse pinned GitHub Actions (``owner/repo@<40-hex-sha>``) across ``.github/workflows/*.yml``."""
    if not workflows_dir.is_dir():
        return []
    by_action: Dict[Tuple[str, str], Set[str]] = {}
    for wf_path in sorted(workflows_dir.glob("*.yml")):
        text = wf_path.read_text(encoding="utf-8")
        for action in _USES_RE.findall(text):
            if action.startswith("./"):
                continue
            matched = _ACTION_PIN_RE.fullmatch(action)
            if matched:
                repo, sha = matched.group(1), matched.group(2)
                by_action.setdefault((repo, sha), set()).add(wf_path.name)
            else:
                repo, _, ref = action.partition("@")
                by_action.setdefault((repo, ref or "unpinned"), set()).add(wf_path.name)

    out: List[Dict[str, Any]] = []
    for (repo, sha), wf_names in sorted(by_action.items()):
        out.append({
            "name": repo,
            "version": sha,
            "purl": "pkg:github/%s@%s" % (repo, sha),
            "workflows": sorted(wf_names),
        })
    return out


def collect_bundled_data_files(root: Path) -> List[Dict[str, str]]:
    """Collect first-party prompt blueprints and lexicon/schema files with SHA-256 digests."""
    candidates: List[Path] = []
    prompts_dir = root / "prompts"
    if prompts_dir.is_dir():
        candidates.extend(sorted(prompts_dir.glob("*.md")))

    for rel in (
        "studio/lexicon.json",
        "data/rednote_risk_lexicon.json",
        "schemas/voice_dna.example.json",
        "reference/rednote_anti_ai_and_compliance_lexicon.md",
    ):
        target = root / rel
        if target.is_file():
            candidates.append(target)

    seen: Set[str] = set()
    items: List[Dict[str, str]] = []
    for path in candidates:
        rel_posix = path.relative_to(root).as_posix()
        if rel_posix in seen:
            continue
        seen.add(rel_posix)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        items.append({
            "path": rel_posix,
            "sha256": digest,
        })
    items.sort(key=lambda entry: entry["path"])
    return items


def generate_sbom(
    root: Path = ROOT,
    *,
    version: Optional[str] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """Build a deterministic CycloneDX 1.5 SBOM dictionary for ``root``."""
    app_version = resolve_version(root, version)
    py_scan = scan_python_imports(root)
    if strict and py_scan["unexpected_third_party"]:
        bad = ", ".join(
            "%s:%d (%s)" % (e["file"], e["line"], e["module"])
            for e in py_scan["unexpected_third_party"]
        )
        raise ValueError("Third-party Python imports found in stdlib-only directories: %s" % bad)

    root_ref = "pkg:generic/learn-my-voice@%s" % app_version
    root_component: Dict[str, Any] = {
        "type": "application",
        "bom-ref": root_ref,
        "name": "learn-my-voice",
        "version": app_version,
        "description": "Local-first bilingual video script studio for Xiaohongshu (RedNote) creators",
        "licenses": [{"license": {"id": "MIT"}}],
        "externalReferences": [{"type": "vcs", "url": REPO_URL}],
    }

    components: List[Dict[str, Any]] = []
    dependencies_map: Dict[str, List[str]] = {}
    root_depends_on: List[str] = []

    # 1. Python stdlib-only runtime component
    py_ref = "pkg:generic/python-stdlib@3.9+"
    deferred_mods = sorted({e["module"] for e in py_scan["isolated_worker_imports"]})
    components.append({
        "type": "platform",
        "bom-ref": py_ref,
        "name": "python-stdlib",
        "version": "3.9+",
        "scope": "required",
        "description": "Python 3.9+ standard library runtime (zero third-party PyPI packages in studio/ and tools/)",
        "properties": [
            {"name": "learn-my-voice:pypi_packages", "value": str(len(py_scan["unexpected_third_party"]))},
            {"name": "learn-my-voice:runtime_policy", "value": "stdlib-only"},
            {"name": "learn-my-voice:scanned_python_files", "value": str(py_scan["scanned_files"])},
            {"name": "learn-my-voice:stdlib_modules_imported", "value": ",".join(py_scan["stdlib_modules_used"])},
            {"name": "learn-my-voice:isolated_worker_deferred_imports", "value": ",".join(deferred_mods)},
        ],
    })
    root_depends_on.append(py_ref)
    dependencies_map[py_ref] = []

    # 2. Bundled first-party prompt blueprints & lexicon/schema data files
    for data_entry in collect_bundled_data_files(root):
        d_ref = "file:%s" % data_entry["path"]
        components.append({
            "type": "data",
            "bom-ref": d_ref,
            "name": data_entry["path"],
            "version": app_version,
            "scope": "required",
            "hashes": [{"alg": "SHA-256", "content": data_entry["sha256"]}],
        })
        root_depends_on.append(d_ref)
        dependencies_map[d_ref] = []

    # 3. Rust crate dependencies from desktop/src-tauri/Cargo.lock
    crates, direct_crate_refs = parse_cargo_lock(root / "desktop" / "src-tauri" / "Cargo.lock")
    root_depends_on.extend(direct_crate_refs)
    for crate in crates:
        comp: Dict[str, Any] = {
            "type": "library",
            "bom-ref": crate["purl"],
            "name": crate["name"],
            "version": crate["version"],
            "purl": crate["purl"],
            "scope": "required",
        }
        if crate["checksum"]:
            comp["hashes"] = [{"alg": "SHA-256", "content": crate["checksum"]}]
        components.append(comp)
        dependencies_map[crate["purl"]] = crate["depends_on"]

    # 4. Pinned GitHub Actions from .github/workflows/*.yml
    for action in parse_workflow_actions(root / ".github" / "workflows"):
        components.append({
            "type": "application",
            "bom-ref": action["purl"],
            "name": action["name"],
            "version": action["version"],
            "purl": action["purl"],
            "scope": "excluded",
            "properties": [
                {"name": "learn-my-voice:workflows", "value": ",".join(action["workflows"])},
            ],
        })
        dependencies_map[action["purl"]] = []

    dependencies_map[root_ref] = sorted(set(root_depends_on))
    dependencies = [
        {"ref": ref, "dependsOn": dependencies_map[ref]}
        for ref in sorted(dependencies_map)
    ]

    content_fingerprint = hashlib.sha256(
        json.dumps(
            {"root": root_component, "components": components, "dependencies": dependencies},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    serial_uuid = uuid.uuid5(SBOM_NAMESPACE, "%s/sbom/%s" % (REPO_URL, content_fingerprint))

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:%s" % serial_uuid,
        "version": 1,
        "metadata": {
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "learn-my-voice-sbom",
                        "version": app_version,
                    }
                ]
            },
            "component": root_component,
        },
        "components": components,
        "dependencies": dependencies,
    }


def render_sbom_json(sbom: Dict[str, Any]) -> bytes:
    """Serialize ``sbom`` deterministically as UTF-8 JSON bytes."""
    return (json.dumps(sbom, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_sbom(out_path: Path, *, root: Path = ROOT, version: Optional[str] = None) -> Dict[str, Any]:
    """Generate and write ``out_path``; returns summary metadata including SHA-256."""
    sbom = generate_sbom(root, version=version, strict=True)
    payload = render_sbom_json(sbom)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "path": str(out_path),
        "version": sbom["metadata"]["component"]["version"],
        "serialNumber": sbom["serialNumber"],
        "components": len(sbom["components"]),
        "sha256": digest,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic CycloneDX 1.5 SBOM.")
    parser.add_argument("--out", type=Path, required=True, help="Output file path (e.g. dist/sbom.cdx.json)")
    parser.add_argument("--version", default=None, help="Override application version (defaults to studio/__init__.py)")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root directory")
    args = parser.parse_args(argv)

    try:
        summary = write_sbom(args.out, root=args.root.resolve(), version=args.version)
    except ValueError as exc:
        print("SBOM generation failed: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
