from __future__ import annotations

import json
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_ROOT = REPOSITORY_ROOT / "examples" / "automation"
MAINTENANCE_SCRIPT = AUTOMATION_ROOT / "run-maintenance.sh"
LAUNCHD_TEMPLATE = (
    AUTOMATION_ROOT / "org.memory-forest.maintenance.plist.example"
)
SVG_ASSETS = (
    REPOSITORY_ROOT / "docs" / "assets" / "memory-forest-retrieval.svg",
    REPOSITORY_ROOT / "docs" / "assets" / "memory-forest-automation.svg",
)
REAL_TEMPORARY_ROOT = Path(tempfile.gettempdir()).resolve()


class AutomationExampleTests(unittest.TestCase):
    def _environment_with_cli(self, directory: Path) -> dict[str, str]:
        wrapper = directory / "memory-forest"
        wrapper.write_text(
            "#!/bin/sh\n"
            f"exec {shlex.quote(sys.executable)} -m memory_forest \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        environment = os.environ.copy()
        source_path = str(REPOSITORY_ROOT / "src")
        current_python_path = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            source_path
            if not current_python_path
            else source_path + os.pathsep + current_python_path
        )
        environment["MEMORY_FOREST_BIN"] = str(wrapper)
        return environment

    def _initialize_forest(
        self,
        forest: Path,
        environment: dict[str, str],
    ) -> None:
        subprocess.run(
            [
                environment["MEMORY_FOREST_BIN"],
                "--json",
                "init",
                str(forest),
                "--example",
            ],
            check=True,
            capture_output=True,
            cwd=REPOSITORY_ROOT,
            env=environment,
            text=True,
        )

    def test_maintenance_script_has_valid_posix_shell_syntax(self):
        subprocess.run(
            ["/bin/sh", "-n", str(MAINTENANCE_SCRIPT)],
            check=True,
            cwd=REPOSITORY_ROOT,
        )

    def test_maintenance_script_rebuilds_index_and_releases_external_lock(self):
        with tempfile.TemporaryDirectory(dir=REAL_TEMPORARY_ROOT) as raw:
            directory = Path(raw)
            forest = directory / "forest"
            environment = self._environment_with_cli(directory)
            self._initialize_forest(forest, environment)
            completed = subprocess.run(
                ["/bin/sh", str(MAINTENANCE_SCRIPT), str(forest)],
                check=False,
                capture_output=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            results = [
                json.loads(line)
                for line in completed.stdout.splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [result["operation"] for result in results],
                ["index", "doctor"],
            )
            self.assertTrue(all(result["ok"] for result in results))
            self.assertTrue(
                (forest / ".memory-forest" / "index.sqlite3").is_file()
            )
            self.assertFalse(Path(str(forest) + ".maintenance.lock").exists())

    def test_maintenance_script_fails_closed_on_existing_lock(self):
        with tempfile.TemporaryDirectory(dir=REAL_TEMPORARY_ROOT) as raw:
            directory = Path(raw)
            forest = directory / "forest"
            environment = self._environment_with_cli(directory)
            self._initialize_forest(forest, environment)
            lock = Path(str(forest) + ".maintenance.lock")
            lock.mkdir(mode=0o700)
            completed = subprocess.run(
                ["/bin/sh", str(MAINTENANCE_SCRIPT), str(forest)],
                check=False,
                capture_output=True,
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
            )
            self.assertEqual(completed.returncode, 75)
            self.assertIn("could not acquire", completed.stderr)
            self.assertTrue(lock.is_dir())
            self.assertFalse(
                (forest / ".memory-forest" / "index.sqlite3").exists()
            )

    def test_launchd_template_is_valid_and_bounded(self):
        with LAUNCHD_TEMPLATE.open("rb") as handle:
            document = plistlib.load(handle)
        self.assertEqual(document["Label"], "org.memory-forest.maintenance")
        self.assertEqual(
            document["ProgramArguments"],
            [
                "/bin/sh",
                "/absolute/path/to/run-maintenance.sh",
                "/absolute/path/to/private-forest",
            ],
        )
        self.assertEqual(document["Umask"], "077")
        self.assertEqual(
            document["EnvironmentVariables"]["MEMORY_FOREST_BIN"],
            "/absolute/path/to/memory-forest",
        )
        self.assertNotIn("WatchPaths", document)
        self.assertFalse(document["KeepAlive"])
        self.assertFalse(document["RunAtLoad"])

    def test_crontab_uses_absolute_placeholders_and_no_percent_expansion(self):
        body = (AUTOMATION_ROOT / "crontab.example").read_text(
            encoding="utf-8"
        )
        self.assertIn("/absolute/path/to/run-maintenance.sh", body)
        self.assertIn("/absolute/path/to/private-forest", body)
        active_lines = [
            line
            for line in body.splitlines()
            if line.lstrip()[:1].isdigit()
        ]
        self.assertEqual(len(active_lines), 1)
        self.assertNotIn("%", active_lines[0])

    def test_codex_prompt_forbids_canonical_edits_and_fallback_scans(self):
        body = (
            AUTOMATION_ROOT / "codex-scheduled-task-prompt.md"
        ).read_text(encoding="utf-8")
        self.assertIn("<ABSOLUTE_MEMORY_FOREST_BIN>", body)
        self.assertIn(
            'MEMORY_FOREST_BIN="<ABSOLUTE_MEMORY_FOREST_BIN>"',
            body,
        )
        for phrase in (
            "Do not inspect, open, or quote memory bodies yourself",
            "Do not edit files under the numbered 00 through 06 layers.",
            "Do not repair, compact, mark, classify, promote, publish, delete",
            "Do not scan the forest parent",
            "Do not bypass the wrapper's external lock.",
        ):
            self.assertIn(phrase, body)

    def test_svg_assets_are_accessible_self_contained_and_ascii_visible(self):
        for asset in SVG_ASSETS:
            with self.subTest(asset=asset.name):
                root = ET.parse(asset).getroot()
                namespace = "{http://www.w3.org/2000/svg}"
                self.assertIsNotNone(root.find(f"{namespace}title"))
                self.assertIsNotNone(root.find(f"{namespace}desc"))
                tags = {
                    element.tag.removeprefix(namespace)
                    for element in root.iter()
                }
                self.assertFalse(
                    tags.intersection({"foreignObject", "image", "script"})
                )
                visible_text = "".join(root.itertext())
                self.assertTrue(visible_text.isascii())
                font_families = [
                    value
                    for element in root.iter()
                    for key, value in element.attrib.items()
                    if key == "font-family"
                ]
                self.assertTrue(font_families)
                self.assertTrue(
                    all("Segoe UI" in family for family in font_families)
                )
                for element in root.iter():
                    for key, value in element.attrib.items():
                        if key.endswith("href"):
                            self.assertFalse(
                                value.startswith(("http:", "https:", "data:"))
                            )

    def test_retrieval_card_titles_use_bounded_lines(self):
        root = ET.parse(SVG_ASSETS[0]).getroot()
        title_lines = [
            "".join(element.itertext()).strip()
            for element in root.iter()
            if element.attrib.get("class") == "card-title"
        ]
        self.assertTrue(title_lines)
        self.assertTrue(
            all(len(line) <= 16 for line in title_lines),
            title_lines,
        )


if __name__ == "__main__":
    unittest.main()
