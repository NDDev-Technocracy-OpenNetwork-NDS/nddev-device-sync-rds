"""The API witness must follow the selected SDK's coherent Core inputs."""
import copy
import difflib
import hashlib
import os
import tempfile
import importlib.util
from pathlib import Path
import sys
import tomllib
import unittest
from unittest import mock

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("qualification", Path(__file__).with_name("check-library.py"))
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


class CoreSelection(unittest.TestCase):
    def setUp(self):
        self.manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "Cargo.toml").read_text())

    def dependencies(self):
        return self.manifest["target"]['cfg(target_os = "linux")']["dependencies"]

    def test_actual_sdk_manifest_is_qualified(self):
        selected = qualification.owned_core_inputs(self.manifest)
        for name in qualification.CORE_PACKAGES:
            for key in ("git", "rev", "version"):
                self.assertEqual(selected[name][key], self.dependencies()[name][key])

    def test_coordinated_source_and_version_changes_need_no_second_pin(self):
        for index, name in enumerate(qualification.CORE_PACKAGES):
            self.dependencies()[name].update(rev="b" * 40, version=f"=1.2.{index}")
        selected = qualification.owned_core_inputs(self.manifest)
        self.assertEqual({dep["rev"] for dep in selected.values()}, {"b" * 40})
        self.assertEqual({dep["version"] for dep in selected.values()}, {"=1.2.0", "=1.2.1"})

    def test_mixed_source_revisions_are_refused_before_provider_preparation(self):
        self.dependencies()[qualification.CORE_PACKAGES[0]]["rev"] = "b" * 40
        with self.assertRaises(ValueError):
            qualification.owned_core_inputs(self.manifest)

    def test_mutable_alias_and_noncanonical_inputs_are_refused(self):
        original = copy.deepcopy(self.manifest)
        for replacement in ({"rev": "main"}, {"version": "0.0.12"}, {"branch": "main"},
                            {"package": "another-crate"}, {"git": "https://example.invalid/core"}):
            self.manifest = copy.deepcopy(original)
            self.dependencies()[qualification.CORE_PACKAGES[0]].update(replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                qualification.owned_core_inputs(self.manifest)


class CheckedSourcePatches(unittest.TestCase):
    """Source derivation only: no Cargo, network, provider or display execution."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("build_inputs", Path(__file__).with_name("prepare-screen-build.py"))
        self.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.build)
        self.temporary = tempfile.TemporaryDirectory(prefix="nds-source-derivation-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = self.root / "original"
        self.original.mkdir()
        (self.original / "Cargo.toml").write_text("original manifest\n")
        (self.original / "native.rs").write_text("original native body\n")
        (self.original / "retained.rs").write_text("preserved source\n")
        self.destination = self.root / "derived"
        self.patch = self.root / "native.patch"
        self.patch.write_text("".join(difflib.unified_diff(
            ["original native body\n"], ["joined native body\n"],
            fromfile="a/native.rs", tofile="b/native.rs")))
        self.changes = [{"path": "native.rs", "before_sha256": self.sha("original native body\n"),
                         "after_sha256": self.sha("joined native body\n")}]

    @staticmethod
    def sha(text):
        return hashlib.sha256(text.encode()).hexdigest()

    def derive(self):
        self.build.derive(self.original, self.destination, lambda _: "checked manifest\n",
                          self.sha("checked manifest\n"), source_patches=[(self.patch, self.changes)])

    def test_exact_patch_and_repeated_verification_preserve_unrelated_source(self):
        self.derive()
        self.derive()
        self.assertEqual((self.destination / "native.rs").read_text(), "joined native body\n")
        self.assertEqual((self.destination / "retained.rs").read_bytes(), (self.original / "retained.rs").read_bytes())
        self.assertEqual((self.original / "native.rs").read_text(), "original native body\n")

    def test_native_source_bytes_ignore_host_line_ending_policy(self):
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1",
                                         "GIT_CONFIG_KEY_0": "core.autocrlf",
                                         "GIT_CONFIG_VALUE_0": "true"}):
            self.derive()
        self.assertEqual((self.destination / "native.rs").read_bytes(), b"joined native body\n")

    def test_changed_original_is_refused_before_creating_derived_source(self):
        (self.original / "native.rs").write_text("different native source\n")
        with self.assertRaises(ValueError):
            self.derive()
        self.assertFalse(self.destination.exists())

    def test_reused_tampered_source_is_refused_without_overwriting_it(self):
        self.derive()
        changed = self.destination / "retained.rs"
        changed.write_text("foreign change\n")
        with self.assertRaises(ValueError):
            self.derive()
        self.assertEqual(changed.read_text(), "foreign change\n")

    def test_patch_cannot_change_an_undeclared_file(self):
        self.patch.write_text(self.patch.read_text() + "".join(difflib.unified_diff(
            ["preserved source\n"], ["unexpected change\n"],
            fromfile="a/retained.rs", tofile="b/retained.rs")))
        with self.assertRaises(ValueError):
            self.derive()

    def test_path_escape_and_link_target_are_refused_before_derivation(self):
        self.changes[0]["path"] = "../outside.rs"
        with self.assertRaises(ValueError):
            self.derive()
        self.assertFalse(self.destination.exists())
        self.changes[0]["path"] = "native.rs"
        (self.original / "native.rs").unlink()
        (self.original / "native.rs").symlink_to(self.original / "retained.rs")
        with self.assertRaises(ValueError):
            self.derive()
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
