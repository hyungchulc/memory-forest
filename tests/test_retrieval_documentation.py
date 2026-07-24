from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "retrieval-guide.md"
LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


class RetrievalDocumentationTests(unittest.TestCase):
    def test_guide_covers_the_public_retrieval_contract(self) -> None:
        body = GUIDE.read_text(encoding="utf-8")
        for heading in (
            "## 1. Intake: select a root, then treat the query as data",
            "## 3. Optional multilingual QueryPlan probes",
            "## 4. Generate and rank bounded candidates deterministically",
            "## 5. Materialize the canonical trail root-first",
            "## 6. Verify source state, then keep body access explicit",
            "## 7. Freshness, conflicts, no evidence, and chronology fallback",
            "## 8. Hybrid and external integration boundary",
            "## 9. Evaluate the two questions separately",
        ):
            self.assertIn(heading, body)
        self.assertIn("01 XLTM -> 02 LTM -> 03 MTM -> 04 STM", body)
        self.assertIn("`05 daily` and `06 istm` are not implicit `retrieve` candidates", body)

    def test_relative_markdown_links_in_guide_resolve(self) -> None:
        body = GUIDE.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(body):
            target = raw_target.split("#", maxsplit=1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            with self.subTest(target=raw_target):
                self.assertTrue((GUIDE.parent / target).is_file())

    def test_english_and_korean_readmes_link_to_the_guide(self) -> None:
        for name in ("README.md", "README.ko.md"):
            with self.subTest(name=name):
                self.assertIn(
                    "docs/retrieval-guide.md",
                    (ROOT / name).read_text(encoding="utf-8"),
                )
