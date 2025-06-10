import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import pwd
import stat
import subprocess
import tempfile

# Assuming utils.py is in /sensos/lib, adjust import path as needed
import sys

sys.path.insert(0, "/sensos_lib")
import utils


class TestUtils(unittest.TestCase):

    def test_run_command_success(self):
        with patch("subprocess.check_output", return_value="hello\n"):
            result = utils.run_command("echo hello")
            self.assertEqual(result, "hello")

    def test_run_command_failure(self):
        with patch("subprocess.check_output", side_effect=Exception("Boom")):
            result = utils.run_command("bad command")
            self.assertTrue(result.startswith("ERROR:"))

    def test_get_basic_auth(self):
        encoded = utils.get_basic_auth("secret")
        self.assertEqual(encoded, "OnNlY3JldA==".encode("utf-8").decode("utf-8"))

    def test_remove_nulls(self):
        data = {"a": "abc\x00", "b": ["foo\x00", "bar"], "c": 42}
        cleaned = utils.remove_nulls(data)
        self.assertEqual(cleaned["a"], "abc")
        self.assertEqual(cleaned["b"][0], "foo")

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
        self.assertIsNone(utils.compute_api_server_wg_ip("bad.ip"))

    def test_compute_hostname(self):
        self.assertEqual(utils.compute_hostname("meshnet", "10.42.3.7"), "meshnet-3-7")

    def test_compute_hostname_invalid_ip(self):
        with patch("sys.stderr", new_callable=MagicMock()):
            self.assertIsNone(utils.compute_hostname("badnet", "10.42"))

    def test_read_network_conf_missing(self):
        with patch("os.path.exists", return_value=False):
            with patch("sys.stderr", new_callable=MagicMock()):
                self.assertEqual(utils.read_network_conf(), {})

    def test_read_network_conf_valid(self):
        mock_data = "SERVER_WG_IP=1.2.3.4\nNETWORK_NAME=testnet\n"
        with patch("builtins.open", mock_open(read_data=mock_data)):
            with patch("os.path.exists", return_value=True):
                result = utils.read_network_conf()
                self.assertEqual(result["SERVER_WG_IP"], "1.2.3.4")
                self.assertEqual(result["NETWORK_NAME"], "testnet")

    def test_safe_cmd_output_success(self):
        with patch("subprocess.check_output", return_value="42\n"):
            self.assertEqual(utils.safe_cmd_output("echo 42"), "42")

    def test_parses_valid_config(self):
        content = """
            # This is a comment
            KEY1=value1
            KEY2 = value with spaces
            KEY3=123

            # Another comment
            KEY4 = true

            """

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            result = utils.read_kv_config(temp_path)
            self.assertEqual(result["KEY1"], "value1")
            self.assertEqual(result["KEY2"], "value with spaces")
            self.assertEqual(result["KEY3"], "123")
            self.assertEqual(result["KEY4"], "true")
            self.assertNotIn("#", result)
        finally:
            os.remove(temp_path)

    def test_returns_empty_dict_if_missing(self):
        path = "/tmp/does-not-exist.conf"
        self.assertEqual(utils.read_kv_config(path), {})

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

    def test_get_client_wg_ip_file_not_found(self):
        with patch("os.path.exists", return_value=False):
            self.assertIsNone(utils.get_client_wg_ip())

    def test_set_permissions_and_owner(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            import getpass

            user = getpass.getuser()
            utils.set_permissions_and_owner(tmp_path, 0o600, user=user)

            st = os.stat(tmp_path)
            mode = stat.S_IMODE(st.st_mode)
            actual_user = pwd.getpwuid(st.st_uid).pw_name

            self.assertEqual(mode, 0o600)
            self.assertEqual(actual_user, user)
        finally:
            os.remove(tmp_path)

    @patch("utils.set_permissions_and_owner")
    @patch("subprocess.run")
    def test_sudo_write_file(self, mock_run, mock_set_permissions):
        content = "[Interface]\nAddress = 10.0.0.2/32\n"
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            dest_path = tmp.name

        try:
            # Clear out the destination file
            os.remove(dest_path)

            utils.sudo_write_file(content, dest_path, mode=0o600, user="root")

            # Check that sudo mv was called
            mock_run.assert_called()
            called_commands = [call.args[0] for call in mock_run.call_args_list]
            self.assertTrue(
                any(cmd[0] == "sudo" and "mv" in cmd for cmd in called_commands)
            )

            # Check that permissions were set
            mock_set_permissions.assert_called_with(
                dest_path, 0o600, user="root", group=None
            )
        finally:
            if os.path.exists(dest_path):
                os.remove(dest_path)

    @patch("utils.subprocess.check_output", return_value="done\n")
    def test_run_sudo_command_as_root(self, mock_check_output):
        with patch("utils.is_root", return_value=True):
            result = utils.run_sudo_command("echo done")
            self.assertEqual(result, "done")
            mock_check_output.assert_called_with("echo done", shell=True, text=True)

    @patch("utils.subprocess.check_output", return_value="done\n")
    def test_run_sudo_command_as_user(self, mock_check_output):
        with patch("utils.is_root", return_value=False):
            result = utils.run_sudo_command("echo done")
            self.assertEqual(result, "done")
            mock_check_output.assert_called_with(
                "sudo echo done", shell=True, text=True
            )

    @patch("utils.subprocess.run")
    def test_run_sudo_shell_as_root(self, mock_run):
        with patch("utils.is_root", return_value=True):
            utils.run_sudo_shell("whoami")
            mock_run.assert_called_with("whoami", shell=True, check=True)

    @patch("utils.subprocess.run")
    def test_run_sudo_shell_as_user(self, mock_run):
        with patch("utils.is_root", return_value=False):
            utils.run_sudo_shell("whoami")
            mock_run.assert_called_with("sudo whoami", shell=True, check=True)


if __name__ == "__main__":
    unittest.main()
