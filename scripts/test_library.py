"""The API witness must follow the selected SDK's coherent Core inputs."""
import copy
import importlib.util
from pathlib import Path
import sys
import tomllib
import unittest

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


if __name__ == "__main__":
    unittest.main()
