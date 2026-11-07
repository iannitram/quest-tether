"""install.sh and uninstall.sh with fake sudo and systemctl (nothing real is touched): python -m unittest discover -s tests"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "packaging", "linux")
FAKE = '#!/bin/sh\necho "$(basename "$0") $*" >> "$QT_LOG"\n'
PKG_FILES = ["quest-tether", "qtetherd", "quest-tether.service", "51-quest-tether.rules",
             "quest-tether-decky.zip", "tools/adb", "tools/gnirehtet", "tools/gnirehtet.apk"]


@unittest.skipUnless(sys.platform.startswith("linux"), "shell scripts run on Linux")
class Scripts(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.home = os.path.join(self.tmp, "home")
        self.pkg = os.path.join(self.tmp, "pkg")
        self.fakebin = os.path.join(self.tmp, "bin")
        self.log = os.path.join(self.tmp, "log")
        for d in (self.home, self.fakebin, os.path.join(self.pkg, "tools")):
            os.makedirs(d)
        for name in PKG_FILES:
            with open(os.path.join(self.pkg, name), "w") as f:
                f.write("x")
        for name in ("sudo", "systemctl"):
            p = os.path.join(self.fakebin, name)
            with open(p, "w") as f:
                f.write(FAKE)
            os.chmod(p, 0o755)
        self.prefix = os.path.join(self.home, ".local/share/quest-tether")
        self.unit = os.path.join(self.home, ".config/systemd/user/quest-tether.service")
        self.bin = os.path.join(self.home, ".local/bin")
        self.link = os.path.join(self.bin, "quest-tether")

    def run_script(self, name):
        env = {"HOME": self.home, "PATH": self.fakebin + ":/usr/bin:/bin", "QT_LOG": self.log, "QT_PKG": self.pkg}
        r = subprocess.run(["sh", os.path.join(SCRIPTS, name)], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def calls(self):
        return open(self.log).read().splitlines() if os.path.exists(self.log) else []

    def test_install_copies_the_files_and_the_unit(self):
        self.run_script("install.sh")
        for name in PKG_FILES:
            if name not in ("51-quest-tether.rules", "quest-tether.service"):
                self.assertTrue(os.path.isfile(os.path.join(self.prefix, name)), name)
        self.assertTrue(os.path.isfile(self.unit))
        for name in ("quest-tether", "qtetherd"):
            self.assertTrue(os.stat(os.path.join(self.prefix, name)).st_mode & stat.S_IXUSR, name)

    def test_install_starts_the_user_service(self):
        self.run_script("install.sh")
        sc = [c for c in self.calls() if c.startswith("systemctl")]
        self.assertEqual(sc, ["systemctl --user daemon-reload", "systemctl --user enable --now quest-tether.service"])

    def test_install_asks_for_sudo_once_and_only_for_the_udev_rule(self):
        out = self.run_script("install.sh")
        sudo = [c for c in self.calls() if c.startswith("sudo")]
        self.assertEqual(len(sudo), 1)
        self.assertIn(os.path.join(self.pkg, "51-quest-tether.rules"), sudo[0])
        self.assertIn("/etc/udev/rules.d/", sudo[0])
        self.assertIn("udevadm control --reload-rules", sudo[0])
        self.assertIn("USB rule", out)

    def test_uninstall_removes_files_unit_and_rule(self):
        self.run_script("install.sh")
        os.remove(self.log)
        self.run_script("uninstall.sh")
        self.assertFalse(os.path.exists(self.prefix))
        self.assertFalse(os.path.exists(self.unit))
        calls = self.calls()
        self.assertEqual([c for c in calls if c.startswith("systemctl")], [
            "systemctl --user disable --now quest-tether.service", "systemctl --user daemon-reload"])
        sudo = [c for c in calls if c.startswith("sudo")]
        self.assertEqual(len(sudo), 1)
        self.assertIn("rm -f /etc/udev/rules.d/51-quest-tether.rules", sudo[0])
        self.assertIn("udevadm control --reload-rules", sudo[0])

    def test_uninstall_on_a_machine_without_the_package_still_succeeds(self):
        self.run_script("uninstall.sh")

    def test_install_links_the_command_into_local_bin(self):
        self.run_script("install.sh")
        self.assertTrue(os.path.islink(self.link))
        self.assertEqual(os.readlink(self.link), os.path.join(self.prefix, "quest-tether"))

    def test_install_points_at_path_when_local_bin_is_not_on_it(self):
        out = self.run_script("install.sh")
        self.assertIn("PATH", out)

    def test_install_leaves_a_file_of_that_name_in_local_bin_alone(self):
        os.makedirs(self.bin)
        with open(self.link, "w") as f:
            f.write("someone else's tool")
        out = self.run_script("install.sh")
        self.assertFalse(os.path.islink(self.link))
        with open(self.link) as f:
            self.assertEqual(f.read(), "someone else's tool")
        self.assertIn("already there", out)

    def test_uninstall_removes_the_command_link(self):
        self.run_script("install.sh")
        self.run_script("uninstall.sh")
        self.assertFalse(os.path.lexists(self.link))

    def test_uninstall_leaves_a_file_of_that_name_alone(self):
        os.makedirs(self.bin)
        with open(self.link, "w") as f:
            f.write("someone else's tool")
        self.run_script("uninstall.sh")
        with open(self.link) as f:
            self.assertEqual(f.read(), "someone else's tool")

    def test_uninstall_leaves_a_link_to_somewhere_else_alone(self):
        os.makedirs(self.bin)
        other = os.path.join(self.tmp, "elsewhere")
        os.symlink(other, self.link)
        self.run_script("uninstall.sh")
        self.assertEqual(os.readlink(self.link), other)

    # claude mcp add/remove (#75): a fake `claude` on PATH logs its arguments.

    def add_fake_claude(self, fail=False):
        body = FAKE + ("exit 1\n" if fail else "")
        p = os.path.join(self.fakebin, "claude")
        with open(p, "w") as f:
            f.write(body)
        os.chmod(p, 0o755)

    def server_path(self):
        return os.path.join(self.prefix, "quest-tether")

    def test_install_registers_the_mcp_server_with_claude(self):
        self.add_fake_claude()
        out = self.run_script("install.sh")
        calls = self.calls()
        self.assertIn("claude mcp remove quest-tether --scope user", calls)
        self.assertIn(
            "claude mcp add --scope user quest-tether -- %s mcp" % self.server_path(), calls)
        self.assertIn("Registered the quest-tether MCP server", out)

    def test_install_registers_the_mcp_server_by_absolute_path_not_by_name(self):
        self.add_fake_claude()
        self.run_script("install.sh")
        add = [c for c in self.calls() if c.startswith("claude mcp add")]
        self.assertEqual(len(add), 1)
        self.assertTrue(os.path.isabs(add[0].split(" -- ")[1].split()[0]))

    def test_install_skips_the_registration_quietly_without_claude(self):
        out = self.run_script("install.sh")
        self.assertFalse([c for c in self.calls() if c.startswith("claude")])
        self.assertNotIn("MCP", out)
        self.assertTrue(os.path.islink(self.link))

    def test_install_still_succeeds_when_claude_refuses_the_registration(self):
        self.add_fake_claude(fail=True)
        out = self.run_script("install.sh")
        self.assertIn("Could not register the MCP server", out)
        self.assertIn("claude mcp add --scope user quest-tether -- %s mcp" % self.server_path(), out)
        self.assertEqual(len([c for c in self.calls() if c.startswith("sudo")]), 1)
        self.assertTrue(os.path.islink(self.link))

    def test_uninstall_removes_the_mcp_registration(self):
        self.add_fake_claude()
        self.run_script("install.sh")
        os.remove(self.log)
        self.run_script("uninstall.sh")
        self.assertIn("claude mcp remove quest-tether --scope user", self.calls())

    def test_uninstall_without_claude_is_quiet_and_still_removes_the_files(self):
        self.run_script("install.sh")
        out = self.run_script("uninstall.sh")
        self.assertFalse([c for c in self.calls() if c.startswith("claude")])
        self.assertFalse(os.path.exists(self.prefix))
        self.assertNotIn("MCP", out)


if __name__ == "__main__":
    unittest.main()
