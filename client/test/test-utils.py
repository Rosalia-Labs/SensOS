import unittest
from unittest.mock import patch, mock_open, MagicMock, call
import os
import pwd
import grp
import stat
import subprocess
import tempfile
import shutil
import getpass

import sys

sys.path.insert(0, "/sensos_lib")
import utils


class TestUtils(unittest.TestCase):
    def test_privileged_shell_success(self):
        with patch("subprocess.check_output", return_value="ok\n"):
            out, rc = utils.privileged_shell("echo ok")
            self.assertEqual(out, "ok")
            self.assertEqual(rc, 0)

    def test_privileged_shell_fallback_sudo(self):
        # Fail first, succeed with sudo
        with patch("subprocess.check_output") as mock_chk:
            mock_chk.side_effect = [subprocess.CalledProcessError(1, "cmd"), "ok\n"]
            out, rc = utils.privileged_shell("echo ok")
            self.assertEqual(out, "ok")
            self.assertEqual(rc, 0)
            self.assertEqual(mock_chk.call_count, 2)
            self.assertIn("sudo", mock_chk.call_args[0][0])

    def test_privileged_shell_fails(self):
        with patch("subprocess.check_output") as mock_chk:
            mock_chk.side_effect = subprocess.CalledProcessError(1, "cmd")
            out, rc = utils.privileged_shell("false")
            self.assertIsNone(out)
            self.assertEqual(rc, 1)

    def test_remove_dir_python(self):
        with tempfile.TemporaryDirectory() as d:
            subdir = os.path.join(d, "sub")
            os.mkdir(subdir)
            utils.remove_dir(subdir)
            self.assertFalse(os.path.exists(subdir))

    def test_remove_dir_sudo(self):
        with patch("shutil.rmtree", side_effect=PermissionError()):
            with patch("utils.privileged_shell") as mock_priv:
                utils.remove_dir("/should/use/sudo")
                mock_priv.assert_called_with("rm -rf /should/use/sudo", silent=True)

    def test_create_dir_python(self):
        with tempfile.TemporaryDirectory() as d:
            testdir = os.path.join(d, "foo")
            utils.create_dir(testdir, owner=getpass.getuser(), mode=0o700)
            self.assertTrue(os.path.isdir(testdir))
            self.assertEqual(stat.S_IMODE(os.stat(testdir).st_mode), 0o700)

    def test_create_dir_sudo(self):
        with patch("os.makedirs", side_effect=PermissionError()):
            with patch("utils.privileged_shell") as mock_priv:
                utils.create_dir("/needs/sudo", owner="root", mode=0o755)
                self.assertTrue(mock_priv.call_count >= 3)

    def test_remove_file_python(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        utils.remove_file(path)
        self.assertFalse(os.path.exists(path))

    def test_remove_file_sudo(self):
        with patch("os.remove", side_effect=PermissionError()):
            with patch("utils.privileged_shell") as mock_priv:
                utils.remove_file("/needs/sudo/rm")
                mock_priv.assert_called_with("rm -f /needs/sudo/rm", silent=True)

    def test_any_files_in_dir_python(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(utils.any_files_in_dir(d))
            with open(os.path.join(d, "test"), "w") as f:
                f.write("x")
            self.assertTrue(utils.any_files_in_dir(d))

    def test_any_files_in_dir_sudo(self):
        with patch("os.listdir", side_effect=PermissionError()):
            with patch("utils.privileged_shell", return_value=("testfile", 0)):
                self.assertTrue(utils.any_files_in_dir("/needs/sudo/ls"))

    def test_read_file_python(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tmp:
            tmp.write("hello")
            tmp_path = tmp.name
        self.assertEqual(utils.read_file(tmp_path), "hello")
        os.remove(tmp_path)

    def test_read_file_sudo(self):
        with patch("builtins.open", side_effect=PermissionError()):
            with patch("utils.privileged_shell", return_value=("abc", 0)):
                self.assertEqual(utils.read_file("/sudo/file"), "abc")

    def test_write_file_python(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        utils.write_file(tmp_path, "hi", mode=0o600, user=getpass.getuser())
        with open(tmp_path) as f:
            self.assertEqual(f.read(), "hi")
        st = os.stat(tmp_path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        os.remove(tmp_path)

    def test_write_file_sudo(self):
        # simulate all chown/chmod/move as PermissionError, uses fallback
        with patch("shutil.move", side_effect=PermissionError()):
            with patch("utils.privileged_shell") as mock_priv:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    testfile = tmp.name
                utils.write_file(testfile, "zzz", mode=0o644, user="root")
                # Expect at least move/chmod/chown to be called via privileged_shell
                calls = [c[0][0] for c in mock_priv.call_args_list]
                self.assertTrue(any("mv" in x for x in calls))
                os.remove(testfile)

    # Existing config and network/kv parsing/util tests...
    def test_get_basic_auth(self):
        # "secret" → b':secret' base64 → OnNlY3JldA==
        self.assertEqual(utils.get_basic_auth("secret"), "OnNlY3JldA==")

    def test_remove_nulls(self):
        data = {"a": "abc\x00", "b": ["foo\x00", "bar"], "c": 42}
        cleaned = utils.remove_nulls(data)
        self.assertEqual(cleaned["a"], "abc")
        self.assertEqual(cleaned["b"][0], "foo")
        self.assertEqual(cleaned["c"], 42)

    def test_read_api_password_missing(self):
        with patch("os.path.exists", return_value=False):
            self.assertIsNone(utils.read_api_password())

    def test_read_api_password_present(self):
        with patch("builtins.open", mock_open(read_data="pw123")):
            with patch("os.path.exists", return_value=True):
                self.assertEqual(utils.read_api_password(), "pw123")

    def test_compute_api_server_wg_ip_valid(self):
        self.assertEqual(utils.compute_api_server_wg_ip("10.42.3.7"), "10.42.0.1")

    def test_compute_api_server_wg_ip_invalid(self):
        with patch("sys.stderr", new_callable=MagicMock()):
            self.assertIsNone(utils.compute_api_server_wg_ip("bad.ip"))

    def test_read_kv_config_valid(self):
        content = """
            # Comment
            KEY1=value1
            KEY2 = value with spaces
            KEY3=123
        """
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write(content)
            f.flush()
            temp_path = f.name
        try:
            result = utils.read_kv_config(temp_path)
            self.assertEqual(result["KEY1"], "value1")
            self.assertEqual(result["KEY2"], "value with spaces")
            self.assertEqual(result["KEY3"], "123")
            self.assertNotIn("#", result)
        finally:
            os.remove(temp_path)

    def test_read_kv_config_missing(self):
        self.assertEqual(utils.read_kv_config("/not/here.conf"), {})

    def test_get_client_wg_ip_present(self):
        mock_data = "CLIENT_WG_IP=10.88.1.1\nSERVER_WG_IP=10.88.0.1\n"
        with patch("builtins.open", mock_open(read_data=mock_data)):
            with patch("os.path.exists", return_value=True):
                self.assertEqual(utils.get_client_wg_ip(), "10.88.1.1")

    def test_get_client_wg_ip_missing(self):
        mock_data = "SERVER_WG_IP=10.88.0.1\n"
        with patch("builtins.open", mock_open(read_data=mock_data)):
            with patch("os.path.exists", return_value=True):
                self.assertIsNone(utils.get_client_wg_ip())

    def test_set_permissions_and_owner(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            user = getpass.getuser()
            utils.set_permissions_and_owner(tmp_path, 0o600, user=user)
            st = os.stat(tmp_path)
            mode = stat.S_IMODE(st.st_mode)
            actual_user = pwd.getpwuid(st.st_uid).pw_name
            self.assertEqual(mode, 0o600)
            self.assertEqual(actual_user, user)
        finally:
            os.remove(tmp_path)

    def test_setup_logging(self):
        # Just make sure no error, no actual logging validated here.
        utils.setup_logging("testutils.log")

    def test_any_files_in_dir_not_found(self):
        self.assertFalse(utils.any_files_in_dir("/definitely/not/found"))


if __name__ == "__main__":
    unittest.main()
