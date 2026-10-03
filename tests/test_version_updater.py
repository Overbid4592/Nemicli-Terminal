"""Offline-Tests für /version und /update (keine Netz-Zugriffe, Git nur lokal im Temp-Ordner).

python -m unittest discover -s tests -p test_version_updater.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "engines", "tools", "ui"):
    sys.path.insert(0, str(ROOT / sub))

import version as VER      # noqa: E402
import updater             # noqa: E402


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "core.safecrlf=false", "-c", "user.email=t@t",
                           "-c", "user.name=t", *args], cwd=cwd, capture_output=True, text=True)


class VersionTests(unittest.TestCase):
    def test_report_has_core_rows(self):
        keys = [k for k, _ in VER.report(model="m", persona="p")]
        for k in ("Version", "Build", "Python", "System", "Datenordner", "Modell", "Persönlichkeit"):
            self.assertIn(k, keys)
        self.assertTrue(VER.build_label().startswith(VER.VERSION))

    def test_build_nummer_wie_windows(self):
        from unittest import mock
        with mock.patch.object(VER, "VERSION", "5.2 Alpha"), \
                mock.patch.object(VER, "_BUILD_CACHE", None), \
                mock.patch.object(VER, "_FROZEN", False), \
                mock.patch.object(VER, "_git", return_value="412"):
            self.assertEqual(VER.build_nummer(), "5.2.412")
        with tempfile.TemporaryDirectory() as d:          # exe: Nummer aus build.txt
            (Path(d) / "build.txt").write_text("5.2.99\n", encoding="utf-8")
            with mock.patch.object(VER, "VERSION", "5.2 Alpha"), \
                    mock.patch.object(VER, "_BUILD_CACHE", None), \
                    mock.patch.object(VER, "_FROZEN", True), \
                    mock.patch.object(VER, "_INSTALL", Path(d)):
                self.assertEqual(VER.build_nummer(), "5.2.99")


@unittest.skipIf(shutil.which("git") is None, "git fehlt")
class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        t = Path(self._tmp.name)
        _git(t, "init", "-q", "--bare", "origin.git")
        _git(t, "clone", "-q", str(t / "origin.git"), "work")
        self.work = t / "work"
        (self.work / "a.txt").write_text("1\n")
        (self.work / "requirements.txt").write_text("rich\n")
        _git(self.work, "add", "-A"); _git(self.work, "commit", "-qm", "init")
        _git(self.work, "push", "-q", "origin", "HEAD:master")
        _git(self.work, "branch", "-q", "-M", "master")
        _git(self.work, "branch", "-q", "--set-upstream-to=origin/master", "master")
        _git(t, "clone", "-q", str(t / "origin.git"), "other")
        self.other = t / "other"
        # Der Updater sieht auf INSTALL statt ROOT: er zieht das
        # PROGRAMM per git nach, nicht den Daten-Ordner des Nutzers.
        self._old_root, self._old_frozen = updater.INSTALL, updater.FROZEN
        updater.ROOT = updater.INSTALL = self.work
        updater.FROZEN = False

    def tearDown(self):
        updater.ROOT = updater.INSTALL = self._old_root
        updater.FROZEN = self._old_frozen
        self._tmp.cleanup()

    def _push_update(self, touch_requirements: bool):
        (self.other / "a.txt").write_text("2\n")
        if touch_requirements:
            (self.other / "requirements.txt").write_text("rich\nhttpx\n")
        _git(self.other, "add", "-A"); _git(self.other, "commit", "-qm", "upd")
        _git(self.other, "push", "-q", "origin", "master")

    def test_ready_and_up_to_date(self):
        self.assertIsNone(updater.why_not())
        r = updater.run(lambda m: None)
        self.assertTrue(r["ok"]); self.assertFalse(r["changed"])

    def test_pull_detects_files_and_requirements(self):
        self._push_update(touch_requirements=True)
        r = updater.run(lambda m: None)
        self.assertTrue(r["ok"] and r["changed"])
        self.assertNotEqual(r["before"], r["after"])
        self.assertIn("requirements.txt", r["files"])
        self.assertTrue(r["requirements"])
        self.assertEqual((self.work / "a.txt").read_text(), "2\n")

    def test_dirty_tree_is_refused(self):
        (self.work / "a.txt").write_text("lokal\n")
        r = updater.run(lambda m: None)
        self.assertFalse(r["ok"]); self.assertIn("nicht committete", r["output"])

    def test_frozen_and_no_repo(self):
        updater.FROZEN = True
        self.assertIn("exe", updater.why_not())
        updater.FROZEN = False
        updater.INSTALL = Path(self._tmp.name)       # kein .git
        self.assertIn("Kein Git-Repo", updater.why_not())


if __name__ == "__main__":
    unittest.main()
