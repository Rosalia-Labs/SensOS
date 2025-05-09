import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
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

    def test_safe_cmd_output_with_sudo_fallback(self):
        def side_effect(cmd, shell, text):
            if "sudo" in cmd:
                return "42\n"
            raise subprocess.CalledProcessError(1, cmd)

        with patch("subprocess.check_output", side_effect=side_effect):
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


if __name__ == "__main__":
    unittest.main()
