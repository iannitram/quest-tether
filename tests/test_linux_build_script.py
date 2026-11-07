"""packaging/linux/build.sh with fake pyinstaller, pnpm and zip on PATH: nothing is really built.

Python -m unittest discover -s tests. The shell parts run on Linux only; the spec checks run anywhere.
"""
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_linux_install_scripts import PKG_FILES  # noqa: E402
ROOT = os.path.join(HERE, "..")
BUILD_SH = os.path.join(ROOT, "packaging", "linux", "build.sh")
SPEC = os.path.join(ROOT, "build-linux.spec")
NAME = "quest-tether"
EXTRA = ["install.sh", "uninstall.sh", "README.md"]

# Each fake logs its arguments and the folder it ran in; the fake pyinstaller writes the two binaries.
FAKE_PYINSTALLER = '''#!/bin/sh
echo "pyinstaller $*" >> "$QT_LOG"
dist=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--distpath" ]; then dist="$a"; fi
  prev="$a"
done
mkdir -p "$dist"
for n in quest-tether qtetherd; do
  printf 'binary %s\\n' "$n" > "$dist/$n"
done
'''

FAKE_PNPM = '''#!/bin/sh
echo "pnpm $* cwd=$(pwd)" >> "$QT_LOG"
if [ "$1" = "run" ] && [ "$2" = "build" ]; then
  mkdir -p dist
  echo "export {};" > dist/index.js
fi
'''

# zip -qr OUT FOLDER: the fake makes an empty OUT file, so the tests check the arguments only.
FAKE_ZIP = '''#!/bin/sh
echo "zip $* cwd=$(pwd)" >> "$QT_LOG"
: > "$2"
'''


def write_exec(path, body):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


@unittest.skipUnless(sys.platform.startswith("linux"), "the build script is a shell script for Linux")
class BuildScript(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.fakebin = os.path.join(self.tmp, "bin")
        self.tools = os.path.join(self.tmp, "tools")
        self.decky = os.path.join(self.tmp, "decky")
        self.build = os.path.join(self.tmp, "build")
        self.dist = os.path.join(self.tmp, "dist")
        self.log = os.path.join(self.tmp, "log")
        for d in (self.fakebin, self.tools, os.path.join(self.decky, "src")):
            os.makedirs(d)
        write_exec(os.path.join(self.fakebin, "pyinstaller"), FAKE_PYINSTALLER)
        write_exec(os.path.join(self.fakebin, "pnpm"), FAKE_PNPM)
        write_exec(os.path.join(self.fakebin, "zip"), FAKE_ZIP)
        for name in ("adb", "gnirehtet", "gnirehtet.apk"):
            with open(os.path.join(self.tools, name), "w") as f:
                f.write("x")
        with open(os.path.join(self.tools, "README.md"), "w") as f:
            f.write("tools")
        for name, text in (("plugin.json", "{}"), ("main.py", "# backend"), ("package.json", "{}"),
                           ("pnpm-lock.yaml", "lockfileVersion: 9")):
            with open(os.path.join(self.decky, name), "w") as f:
                f.write(text)
        with open(os.path.join(self.decky, "src", "index.tsx"), "w") as f:
            f.write("export {};")

    def env(self):
        return {"HOME": os.path.join(self.tmp, "home"), "PATH": self.fakebin + ":/usr/bin:/bin",
                "QT_LOG": self.log, "QT_BUILD": self.build, "QT_DIST": self.dist,
                "QT_TOOLS": self.tools, "QT_DECKY": self.decky}

    def run_build(self):
        return subprocess.run(["sh", BUILD_SH], env=self.env(), capture_output=True, text=True, timeout=120)

    def build_ok(self):
        r = self.run_build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def calls(self):
        return open(self.log).read().splitlines() if os.path.exists(self.log) else []

    def members(self):
        with tarfile.open(os.path.join(self.dist, NAME + "-linux.tar.gz")) as tf:
            return {m.name: m for m in tf.getmembers()}

    def files_in_tar(self):
        return {n[len(NAME) + 1:] for n, m in self.members().items() if m.isfile()}

    def test_tarball_holds_the_package_files_under_one_folder(self):
        self.build_ok()
        names = self.members()
        self.assertTrue(all(n == NAME or n.startswith(NAME + "/") for n in names), sorted(names))
        self.assertEqual(self.files_in_tar(), set(PKG_FILES) | set(EXTRA))

    def test_binaries_and_scripts_are_executable_in_the_tarball(self):
        self.build_ok()
        members = self.members()
        for name in ("quest-tether", "qtetherd", "install.sh", "uninstall.sh",
                     "tools/adb", "tools/gnirehtet"):
            self.assertTrue(members[NAME + "/" + name].mode & 0o100, name)

    def test_tarball_has_the_native_relay_and_no_jar(self):
        with open(os.path.join(self.tools, "gnirehtet.jar"), "w") as f:
            f.write("java relay")
        self.build_ok()
        files = self.files_in_tar()
        self.assertIn("tools/gnirehtet", files)
        self.assertIn("tools/gnirehtet.apk", files)
        self.assertNotIn("tools/gnirehtet.jar", files)

    def test_pyinstaller_builds_from_the_linux_spec(self):
        self.build_ok()
        pyi = [c for c in self.calls() if c.startswith("pyinstaller")]
        self.assertEqual(len(pyi), 1)
        self.assertTrue(pyi[0].endswith("build-linux.spec"), pyi[0])
        self.assertNotIn(" build.spec", pyi[0])

    def test_decky_zip_is_built_from_the_plugin_folder_and_packaged(self):
        self.build_ok()
        calls = self.calls()
        self.assertIn("pnpm install --frozen-lockfile cwd=%s" % self.decky, calls)
        self.assertIn("pnpm run build cwd=%s" % self.decky, calls)
        zip_calls = [c for c in calls if c.startswith("zip ")]
        self.assertEqual(len(zip_calls), 1)
        self.assertIn("-qr %s/quest-tether-decky.zip quest-tether" % self.build, zip_calls[0])
        folder = os.path.join(self.build, "decky", NAME)
        for name in ("plugin.json", "main.py", os.path.join("dist", "index.js")):
            self.assertTrue(os.path.isfile(os.path.join(folder, name)), name)
        self.assertIn("quest-tether-decky.zip", self.files_in_tar())

    def test_without_pnpm_the_zip_is_skipped_with_a_message(self):
        os.remove(os.path.join(self.fakebin, "pnpm"))
        r = self.build_ok()
        self.assertIn("pnpm is not on PATH", r.stdout)
        self.assertNotIn("quest-tether-decky.zip", self.files_in_tar())
        self.assertEqual(self.files_in_tar(), (set(PKG_FILES) | set(EXTRA)) - {"quest-tether-decky.zip"})

    def test_a_missing_tool_is_named_and_nothing_is_built(self):
        os.remove(os.path.join(self.tools, "gnirehtet.apk"))
        r = self.run_build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tools/gnirehtet.apk", r.stderr)
        self.assertFalse([c for c in self.calls() if c.startswith("pyinstaller")])
        self.assertFalse(os.path.exists(os.path.join(self.dist, NAME + "-linux.tar.gz")))

    def test_a_missing_pyinstaller_is_named(self):
        os.remove(os.path.join(self.fakebin, "pyinstaller"))
        r = self.run_build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pyinstaller is not on PATH", r.stderr)

    def test_the_repo_tools_folder_is_not_changed(self):
        before = sorted(os.listdir(self.tools))
        self.build_ok()
        self.assertEqual(sorted(os.listdir(self.tools)), before)


class LinuxSpec(unittest.TestCase):
    """Static checks on build-linux.spec: it imports PyInstaller's names, so it is read as text."""

    def setUp(self):
        with open(SPEC, encoding="utf-8") as f:
            self.text = f.read()

    def test_it_makes_the_cli_and_the_service_as_console_programs(self):
        self.assertIn("name='quest-tether'", self.text)
        self.assertIn("name='qtetherd'", self.text)
        self.assertIn("['cli.py']", self.text)
        self.assertIn("['qtetherd.py']", self.text)
        self.assertEqual(self.text.count("console=True"), 2)

    def test_it_does_not_use_the_windows_icon_or_pystray_win32_backend(self):
        self.assertNotIn(".ico", self.text)
        self.assertNotIn("_win32", self.text)
        self.assertIn("pystray._xorg", self.text)
        self.assertIn("pystray._appindicator", self.text)

    def test_the_service_binary_leaves_the_window_out(self):
        self.assertIn("excludes=['customtkinter', 'tkinter', 'PIL', 'pystray']", self.text)

    def test_tools_are_not_bundled(self):
        self.assertNotIn("('tools'", self.text)


if __name__ == "__main__":
    unittest.main()
