"""Failure cases for publication tooling; no application test framework is selected."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("prepare", Path(__file__).with_name("prepare.py"))
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PublicationChecks(unittest.TestCase):
    def test_private_and_traversal_paths_are_rejected_before_reading(self):
        for path in ["../outside.md", "/tmp/outside.md", "docs/QUESTIONNAIRE.md",
                     "docs/FOLLOW_UP.md", ".codex/config.toml", ".agents/SKILL.md", ".env"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                prepare.public_path(path)

    def test_symlink_to_an_outside_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside.md").write_text("Private content", encoding="utf-8")
            (root / "public").mkdir()
            (root / "public/link.md").symlink_to(root / "outside.md")
            with patch.object(prepare, "ROOT", root / "public"), self.assertRaises(ValueError):
                prepare.public_path("link.md")

    def test_link_to_a_file_outside_the_manifest_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Link outside publication"):
            prepare.check_text("README.md", b"[private](docs/FOLLOW_UP.md)", {"README.md"})
        prepare.check_text("README.md", b"[brief](docs/PROJECT_BRIEF.md#example)",
                           {"README.md", "docs/PROJECT_BRIEF.md"})

    def test_token_detection_does_not_include_the_token_in_the_error(self):
        token = "ghp_" + "A" * 36
        with self.assertRaises(ValueError) as caught:
            prepare.check_text("README.md", token.encode("utf-8"), {"README.md"})
        self.assertNotIn(token, str(caught.exception))

    def test_personal_email_is_rejected_and_github_noreply_is_allowed(self):
        with self.assertRaises(ValueError):
            prepare.check_text("README.md", b"someone@" + b"example.com", {"README.md"})
        prepare.check_text("README.md", b"10113961+Sphag@users.noreply.github.com", {"README.md"})

    def test_dependency_ranges_and_issue_states_follow_the_backlog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            for completed_count in (3, 4):
                with self.subTest(completed_count=completed_count):
                    body = "\n".join(
                        "### T{:02d} — Task\n\n**Зависимости:** T04–T06.\n\n"
                        "**Готово, когда:** checked.\n\n**Статус:** {}.\n".format(
                            n, "завершена" if n < completed_count else "выполняется")
                        for n in range(16))
                    (root / "docs/BACKLOG.md").write_text(body, encoding="utf-8")
                    with patch.object(prepare, "ROOT", root):
                        issues = prepare.issue_specs()
                    self.assertEqual(issues[7]["dependencies"], ["T04", "T05", "T06"])
                    self.assertEqual([issue["state"] for issue in issues],
                                     ["closed"] * completed_count + ["open"] * (16 - completed_count))
                    self.assertIn("- [ ]", issues[4]["body"])
                    self.assertIn("- [x]", issues[3]["body"])


if __name__ == "__main__":
    unittest.main()
