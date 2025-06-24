import unittest
from unittest.mock import patch, mock_open, MagicMock, call
import os
import sys
import tempfile
import builtins

sys.path.insert(0, "/sensos_lib")
import utils


class TestUtils(unittest.TestCase):

    @patch("utils.subprocess.check_output", return_value="done\n")
    @patch("os.geteuid", return_value=0)
    def test_privileged_shell_root(self, mock_euid, mock_check):
        out, rc = utils.privileged_shell("ls -l")
        self.assertEqual(out, "done")
        self.assertEqual(rc, 0)
        mock_check.assert_called_once_with("ls -l", shell=True, text=True)

    @patch("utils.subprocess.check_output", return_value="done\n")
    @patch("os.geteuid", return_value=1000)
    def test_privileged_shell_nonroot(self, mock_euid, mock_check):
        out, rc = utils.privileged_shell("ls -l")
        self.assertEqual(out, "done")
        self.assertEqual(rc, 0)
        mock_check.assert_called_once_with("sudo ls -l", shell=True, text=True)

    @patch("utils.subprocess.check_output", return_value="ok\n")
    @patch("os.geteuid", return_value=0)
    def test_privileged_shell_root_with_user(self, mock_euid, mock_check):
        out, rc = utils.privileged_shell("whoami", user="nobody")
        self.assertEqual(out, "ok")
        self.assertEqual(rc, 0)
        self.assertTrue(mock_check.call_args[0][0].startswith("su - nobody -c"))

    @patch("utils.subprocess.check_output", return_value="ok\n")
    @patch("os.geteuid", return_value=1000)
    def test_privileged_shell_nonroot_with_user(self, mock_euid, mock_check):
        out, rc = utils.privileged_shell("whoami", user="nobody")
        self.assertEqual(out, "ok")
        self.assertEqual(rc, 0)
        self.assertTrue(mock_check.call_args[0][0].startswith("sudo -u nobody"))

    @patch("utils.privileged_shell")
    def test_remove_dir(self, mock_priv):
        utils.remove_dir("/test/path")
        mock_priv.assert_called_with("rm -rf /test/path", silent=True)

    @patch("utils.privileged_shell")
    def test_create_dir(self, mock_priv):
        utils.create_dir("/foo/bar", owner="alice", mode=0o755)
        calls = [
            call("mkdir -p /foo/bar", silent=True),
            call("chmod 755 /foo/bar", silent=True),
            call("chown alice:alice /foo/bar", silent=True),
        ]
        mock_priv.assert_has_calls(calls, any_order=False)

    @patch("utils.privileged_shell")
    def test_remove_file(self, mock_priv):
        utils.remove_file("/foo/bar.txt")
        mock_priv.assert_called_with("rm -f /foo/bar.txt", silent=True)

    @patch("utils.privileged_shell", return_value=("file1\nfile2\n", 0))
    def test_any_files_in_dir_true(self, mock_priv):
        self.assertTrue(utils.any_files_in_dir("/has/files"))

    @patch("utils.privileged_shell", return_value=("", 0))
    def test_any_files_in_dir_false(self, mock_priv):
        self.assertFalse(utils.any_files_in_dir("/empty"))

    @patch("utils.privileged_shell", return_value=("hello\n", 0))
    def test_read_file(self, mock_priv):
        self.assertEqual(utils.read_file("/foo/bar.txt"), "hello")

    @patch("utils.privileged_shell")
    def test_write_file(self, mock_priv):
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            # Patch NamedTemporaryFile to return a real temp file object
            with patch("tempfile.NamedTemporaryFile", return_value=open(tmp_path, "w")):
                utils.write_file(
                    "/foo.txt", "abc", mode=0o600, user="alice", group="staff"
                )
                # Should call mv, chmod, chown
                calls = [c[0][0] for c in mock_priv.call_args_list]
                self.assertTrue(any("mv" in x for x in calls))
                self.assertTrue(any("chmod" in x for x in calls))
                self.assertTrue(any("chown" in x for x in calls))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("utils.privileged_shell")
    def test_set_permissions_and_owner_with_user(self, mock_priv):
        utils.set_permissions_and_owner("/foo.txt", 0o600, user="bob", group="adm")
        mock_priv.assert_any_call("chmod 600 /foo.txt", silent=True)
        mock_priv.assert_any_call("chown bob:adm /foo.txt", silent=True)

    @patch("utils.privileged_shell")
    def test_set_permissions_and_owner_no_user(self, mock_priv):
        utils.set_permissions_and_owner("/foo.txt", 0o644)
        mock_priv.assert_called_with("chmod 644 /foo.txt", silent=True)

    def test_get_basic_auth(self):
        self.assertEqual(utils.get_basic_auth("hunter2"), "Omh1bnRlcjI=")

    def test_remove_nulls_dict_list(self):
        data = {"a": "foo\x00bar", "b": ["baz\x00", "qux"], "c": 123}
        cleaned = utils.remove_nulls(data)
        self.assertEqual(cleaned["a"], "foobar")
        self.assertEqual(cleaned["b"], ["baz", "qux"])
        self.assertEqual(cleaned["c"], 123)

    @patch("os.path.exists", return_value=False)
    def test_read_api_password_missing(self, mock_exists):
        with patch("sys.stderr", new_callable=MagicMock()):
            self.assertIsNone(utils.read_api_password())

    @patch("os.path.exists", return_value=True)
    @patch("utils.read_file", return_value="pw")
    def test_read_api_password_ok(self, mock_read, mock_exists):
        self.assertEqual(utils.read_api_password(), "pw")

    @patch("os.path.exists", return_value=False)
    def test_detect_wireguard_api_missing(self, mock_exists):
        self.assertEqual(utils.detect_wireguard_api(), (None, None))

    @patch("os.path.exists", return_value=True)
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="SERVER_WG_IP=10.0.0.1\nSERVER_PORT=9999\n",
    )
    @patch("utils.requests.get")
    def test_detect_wireguard_api_ok(self, mock_req, mock_open, mock_exists):
        class Dummy:
            ok = True

        mock_req.return_value = Dummy()
        ip, port = utils.detect_wireguard_api()
        self.assertEqual(ip, "10.0.0.1")
        self.assertEqual(port, "9999")

    @patch("os.path.exists", return_value=False)
    def test_load_defaults_missing(self, mock_exists):
        self.assertEqual(utils.load_defaults("foo"), {})

    @patch("os.path.exists", return_value=True)
    def test_load_defaults_section(self, mock_exists):
        data = "[foo]\na=1\nb=2\n"
        with patch("builtins.open", mock_open(read_data=data)):
            res = utils.load_defaults("foo", path="/tmp/x.conf")
            self.assertIn("a", res)
            self.assertIn("b", res)

    @patch("os.path.exists", return_value=False)
    def test_read_network_conf_missing(self, mock_exists):
        with patch("sys.stderr", new_callable=MagicMock()):
            self.assertEqual(utils.read_network_conf(), {})

    @patch("os.path.exists", return_value=True)
    def test_read_network_conf(self, mock_exists):
        data = "KEY1=VAL1\nKEY2=VAL2\n"
        with patch("builtins.open", mock_open(read_data=data)):
            conf = utils.read_network_conf()
            self.assertEqual(conf, {"KEY1": "VAL1", "KEY2": "VAL2"})

    @patch("utils.requests.get")
    def test_validate_api_password_true(self, mock_get):
        class R:
            status_code = 200

        mock_get.return_value = R()
        self.assertTrue(utils.validate_api_password("host", "1234", "pw"))

    @patch("utils.requests.get")
    def test_validate_api_password_false(self, mock_get):
        class R:
            status_code = 403

        mock_get.return_value = R()
        self.assertFalse(utils.validate_api_password("host", "1234", "pw"))

    def test_compute_api_server_wg_ip_valid(self):
        self.assertEqual(utils.compute_api_server_wg_ip("10.1.2.3"), "10.1.0.1")

    def test_compute_api_server_wg_ip_invalid(self):
        with patch("sys.stderr", new_callable=MagicMock()):
            self.assertIsNone(utils.compute_api_server_wg_ip("not.an.ip"))

    @patch("os.path.exists", return_value=True)
    def test_read_kv_config(self, mock_exists):
        data = """
            # Comment
            KEY1=value1
            KEY2 = value with spaces
            KEY3=123
        """
        with patch("builtins.open", mock_open(read_data=data)):
            res = utils.read_kv_config("/x/y")
            self.assertEqual(res["KEY1"], "value1")
            self.assertEqual(res["KEY2"], "value with spaces")
            self.assertEqual(res["KEY3"], "123")
            self.assertNotIn("#", res)

    @patch("os.path.exists", return_value=False)
    def test_read_kv_config_missing(self, mock_exists):
        self.assertEqual(utils.read_kv_config("/not/here"), {})

    @patch("utils.read_kv_config", return_value={"CLIENT_WG_IP": "10.5.5.7"})
    def test_get_client_wg_ip(self, mock_read_kv):
        self.assertEqual(utils.get_client_wg_ip(), "10.5.5.7")

    @patch("utils.read_kv_config", return_value={})
    def test_get_client_wg_ip_none(self, mock_read_kv):
        self.assertIsNone(utils.get_client_wg_ip())

    def test_tee_write_and_flush(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        t = utils.Tee(path, mode="w")
        t.write("hi\n")
        t.flush()
        t.log.close()
        with open(path) as f:
            self.assertIn("hi", f.read())
        os.remove(path)

    @patch("os.makedirs")
    @patch("os.path.join", return_value="/sensos/log/myprog.log")
    @patch("os.path.basename", return_value="myprog.py")
    def test_setup_logging(self, mock_basename, mock_join, mock_makedirs):
        # Just ensure it runs without error and monkeypatches sys.stdout
        with patch("builtins.open", mock_open()):
            utils.setup_logging("myprog.log")
            self.assertIsInstance(sys.stdout, utils.Tee)


if __name__ == "__main__":
    unittest.main()
