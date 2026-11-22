"""Tests for the deterministic CycloneDX 1.5 SBOM generator (tools/sbom.py)."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import studio

ROOT = Path(__file__).resolve().parent.parent


def _load_sbom_tool():
    spec = importlib.util.spec_from_file_location("sbom_tool", str(ROOT / "tools" / "sbom.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sbom_tool = _load_sbom_tool()


class SbomGeneratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lmv-sbom-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_byte_for_byte_repeatability_and_version_override(self) -> None:
        out_a = self.tmp / "a" / "sbom.cdx.json"
        out_b = self.tmp / "b" / "sbom.cdx.json"

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sbom_tool.main(["--out", str(out_a)]), 0)
            self.assertEqual(sbom_tool.main(["--out", str(out_b)]), 0)

        bytes_a = out_a.read_bytes()
        bytes_b = out_b.read_bytes()
        self.assertEqual(bytes_a, bytes_b)
        self.assertTrue(bytes_a.endswith(b"\n"))

        doc = json.loads(bytes_a.decode("utf-8"))
        self.assertEqual(doc["metadata"]["component"]["version"], studio.__version__)

        out_v1 = self.tmp / "v1" / "sbom.cdx.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sbom_tool.main(["--out", str(out_v1), "--version", "v1.0.0"]), 0)
        doc_v1 = json.loads(out_v1.read_text(encoding="utf-8"))
        self.assertEqual(doc_v1["metadata"]["component"]["version"], "1.0.0")
        self.assertNotEqual(doc_v1["serialNumber"], doc["serialNumber"])

    def test_valid_cyclonedx_1_5_structure_and_dependency_graph(self) -> None:
        doc = sbom_tool.generate_sbom(ROOT)
        self.assertEqual(doc["bomFormat"], "CycloneDX")
        self.assertEqual(doc["specVersion"], "1.5")
        self.assertEqual(doc["version"], 1)
        self.assertRegex(
            doc["serialNumber"],
            r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )

        root_comp = doc["metadata"]["component"]
        self.assertEqual(root_comp["type"], "application")
        self.assertEqual(root_comp["name"], "learn-my-voice")
        self.assertEqual(root_comp["licenses"], [{"license": {"id": "MIT"}}])

        all_refs = [root_comp["bom-ref"]] + [c["bom-ref"] for c in doc["components"]]
        self.assertEqual(len(all_refs), len(set(all_refs)), "Every bom-ref must be unique")

        known_refs = set(all_refs)
        dep_entries = doc["dependencies"]
        self.assertEqual({d["ref"] for d in dep_entries}, known_refs)
        for dep in dep_entries:
            for target in dep["dependsOn"]:
                self.assertIn(target, known_refs, "%s depends on unknown %s" % (dep["ref"], target))

    def test_ast_scan_verifies_zero_third_party_python_imports_and_catches_violations(self) -> None:
        scan = sbom_tool.scan_python_imports(ROOT)
        self.assertGreater(scan["scanned_files"], 40)
        self.assertEqual(scan["unexpected_third_party"], [])
        # Isolated worker deferred imports are explicitly tracked and limited to whisper_worker.py.
        self.assertEqual(
            {(e["file"], e["module"]) for e in scan["isolated_worker_imports"]},
            {("studio/whisper_worker.py", "mlx_whisper"), ("studio/whisper_worker.py", "numpy")},
        )

        doc = sbom_tool.generate_sbom(ROOT)
        py_comp = next(c for c in doc["components"] if c["name"] == "python-stdlib")
        props = {p["name"]: p["value"] for p in py_comp["properties"]}
        self.assertEqual(props["learn-my-voice:pypi_packages"], "0")
        self.assertEqual(props["learn-my-voice:runtime_policy"], "stdlib-only")

        # Synthetic repo with an accidental PyPI import in studio/ must fail strict SBOM generation.
        fake_root = self.tmp / "fake_repo"
        (fake_root / "studio").mkdir(parents=True)
        (fake_root / "tools").mkdir(parents=True)
        (fake_root / "studio" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
        (fake_root / "studio" / "bad.py").write_text("import requests\n", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = sbom_tool.main(["--root", str(fake_root), "--out", str(self.tmp / "bad.json")])
        self.assertEqual(code, 1)
        self.assertIn("requests", err.getvalue())

    def test_cargo_lock_crates_actions_and_bundled_files_are_covered_with_hashes(self) -> None:
        doc = sbom_tool.generate_sbom(ROOT)
        cargo_lock_text = (ROOT / "desktop" / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
        expected_pkg_headers = re.findall(r'^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', cargo_lock_text, re.M)
        expected_external = [(n, v) for n, v in expected_pkg_headers if n != "learn-my-voice"]

        crate_comps = [c for c in doc["components"] if (c.get("purl") or "").startswith("pkg:cargo/")]
        self.assertEqual(len(crate_comps), len(expected_external))
        self.assertGreater(len(crate_comps), 400)
        by_purl = {c["purl"]: c for c in crate_comps}
        for name, ver in expected_external:
            purl = "pkg:cargo/%s@%s" % (name, ver)
            self.assertIn(purl, by_purl)
            hashes = by_purl[purl].get("hashes") or []
            self.assertEqual(len(hashes), 1, purl)
            self.assertEqual(hashes[0]["alg"], "SHA-256")
            self.assertRegex(hashes[0]["content"], r"^[0-9a-f]{64}$")

        # Pinned GitHub Actions
        action_comps = [c for c in doc["components"] if (c.get("purl") or "").startswith("pkg:github/")]
        self.assertGreaterEqual(len(action_comps), 4)
        for act in action_comps:
            self.assertRegex(act["version"], r"^[0-9a-f]{40}$", act["name"])

        # Bundled prompts and lexicon/schema data files
        data_comps = {c["name"]: c for c in doc["components"] if c["type"] == "data"}
        prompt_files = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "prompts").glob("*.md"))
        self.assertGreaterEqual(len(prompt_files), 10)
        for rel in prompt_files + ["studio/lexicon.json", "schemas/voice_dna.example.json"]:
            self.assertIn(rel, data_comps)
            expected_sha = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            self.assertEqual(data_comps[rel]["hashes"][0]["content"], expected_sha, rel)

        # Also picks up data/rednote_risk_lexicon.json when present in a root directory
        custom_root = self.tmp / "custom_root"
        (custom_root / "studio").mkdir(parents=True)
        (custom_root / "data").mkdir(parents=True)
        (custom_root / "prompts").mkdir(parents=True)
        (custom_root / "studio" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
        (custom_root / "prompts" / "01_test.md").write_text("# Prompt\n", encoding="utf-8")
        lex_bytes = b'{"risk_terms": []}\n'
        (custom_root / "data" / "rednote_risk_lexicon.json").write_bytes(lex_bytes)
        custom_doc = sbom_tool.generate_sbom(custom_root)
        custom_data = {c["name"]: c for c in custom_doc["components"] if c["type"] == "data"}
        self.assertIn("data/rednote_risk_lexicon.json", custom_data)
        self.assertEqual(
            custom_data["data/rednote_risk_lexicon.json"]["hashes"][0]["content"],
            hashlib.sha256(lex_bytes).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
