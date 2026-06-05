import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import paramiko
from core import (
    connect_server,
    close_server,
    execute_command,
    get_system_info,
    upload_file,
    download_file,
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    CONNECTION_ERROR_MESSAGE,
    AUTH_FAILED_MESSAGE,
    CONNECT_TIMEOUT_MESSAGE,
    CONNECT_REFUSED_MESSAGE,
    EXECUTE_ERROR_MESSAGE,
    FILE_TRANSFER_ERROR_MESSAGE,
)


class TestConnectServerMocked(unittest.TestCase):
    @patch("core.ssh.paramiko.SSHClient")
    def test_connect_success_returns_client(self, mock_ssh_client):
        mock_instance = MagicMock()
        mock_ssh_client.return_value = mock_instance

        client = connect_server("1.2.3.4", "root", "password", 22)

        self.assertIs(client, mock_instance)
        mock_instance.connect.assert_called_once_with(
            hostname="1.2.3.4",
            port=22,
            username="root",
            password="password",
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        mock_instance.close.assert_not_called()

    @patch("core.ssh.paramiko.SSHClient")
    def test_connect_failure_raises_and_closes(self, mock_ssh_client):
        mock_instance = MagicMock()
        mock_instance.connect.side_effect = Exception("auth failed")
        mock_ssh_client.return_value = mock_instance

        with self.assertRaises(ConnectionError) as ctx:
            connect_server("1.2.3.4", "root", "wrong", 22)

        self.assertEqual(str(ctx.exception), CONNECTION_ERROR_MESSAGE)
        mock_instance.close.assert_called_once()


class TestConnectErrorClassification(unittest.TestCase):
    """三种连接错误分别被精确捕获，并且都还能被 ConnectionError 兜底。"""

    @patch("core.ssh.paramiko.SSHClient")
    def test_auth_failure_raises_auth_failed_error(self, mock_ssh_client):
        import paramiko as _p
        mock_instance = MagicMock()
        mock_instance.connect.side_effect = _p.AuthenticationException("auth failed")
        mock_ssh_client.return_value = mock_instance

        with self.assertRaises(AuthFailedError) as ctx:
            connect_server("1.2.3.4", "root", "wrong_pwd", 22)

        self.assertEqual(str(ctx.exception), AUTH_FAILED_MESSAGE)
        # 还能被 ConnectionError 兜底捕获
        self.assertIsInstance(ctx.exception, ConnectionError)
        mock_instance.close.assert_called_once()

    @patch("core.ssh.paramiko.SSHClient")
    def test_timeout_raises_connect_timeout_error(self, mock_ssh_client):
        import socket
        mock_instance = MagicMock()
        mock_instance.connect.side_effect = socket.timeout("timed out")
        mock_ssh_client.return_value = mock_instance

        with self.assertRaises(ConnectTimeoutError) as ctx:
            connect_server("1.2.3.4", "root", "pwd", 22)

        self.assertEqual(str(ctx.exception), CONNECT_TIMEOUT_MESSAGE)
        self.assertIsInstance(ctx.exception, ConnectionError)

    @patch("core.ssh.paramiko.SSHClient")
    def test_refused_raises_connect_refused_error(self, mock_ssh_client):
        mock_instance = MagicMock()
        mock_instance.connect.side_effect = ConnectionRefusedError(61, "refused")
        mock_ssh_client.return_value = mock_instance

        with self.assertRaises(ConnectRefusedError) as ctx:
            connect_server("1.2.3.4", "root", "pwd", 22)

        self.assertEqual(str(ctx.exception), CONNECT_REFUSED_MESSAGE)
        self.assertIsInstance(ctx.exception, ConnectionError)

    @patch("core.ssh.paramiko.SSHClient")
    def test_unknown_error_falls_back_to_connection_error(self, mock_ssh_client):
        mock_instance = MagicMock()
        mock_instance.connect.side_effect = RuntimeError("something weird")
        mock_ssh_client.return_value = mock_instance

        with self.assertRaises(ConnectionError) as ctx:
            connect_server("1.2.3.4", "root", "pwd", 22)

        # 是基类 ConnectionError，不是细分子类
        self.assertEqual(type(ctx.exception), ConnectionError)
        self.assertEqual(str(ctx.exception), CONNECTION_ERROR_MESSAGE)

    @patch("core.ssh.paramiko.SSHClient")
    def test_business_can_catch_specific_type(self, mock_ssh_client):
        """演示业务层可以区分错误类型给出针对性提示。"""
        import paramiko as _p
        mock_instance = MagicMock()
        mock_instance.connect.side_effect = _p.AuthenticationException("auth")
        mock_ssh_client.return_value = mock_instance

        try:
            connect_server("1.2.3.4", "root", "wrong", 22)
        except AuthFailedError:
            verdict = "check_password"
        except ConnectTimeoutError:
            verdict = "check_port"
        except ConnectionError:
            verdict = "unknown"

        self.assertEqual(verdict, "check_password")


class TestCloseServerMocked(unittest.TestCase):
    def test_close_calls_client_close(self):
        client = MagicMock()
        close_server(client)
        client.close.assert_called_once()

    def test_close_none_is_safe(self):
        close_server(None)

    def test_close_swallows_exception(self):
        client = MagicMock()
        client.close.side_effect = Exception("already closed")
        close_server(client)
        client.close.assert_called_once()


class TestExecuteCommandMocked(unittest.TestCase):
    def _make_client(self, stdout_data: bytes, stderr_data: bytes, exit_code: int):
        client = MagicMock()
        stdout = MagicMock()
        stdout.read.return_value = stdout_data
        stdout.channel.recv_exit_status.return_value = exit_code
        stderr = MagicMock()
        stderr.read.return_value = stderr_data
        client.exec_command.return_value = (MagicMock(), stdout, stderr)
        return client

    def test_execute_returns_stdout_stderr_exit_code(self):
        client = self._make_client(b"hello\n", b"", 0)
        result = execute_command(client, "echo hello")
        self.assertEqual(result, {"stdout": "hello\n", "stderr": "", "exit_code": 0})
        client.exec_command.assert_called_once_with("echo hello", timeout=30)

    def test_execute_captures_nonzero_exit(self):
        client = self._make_client(b"", b"not found\n", 127)
        result = execute_command(client, "nosuchcmd")
        self.assertEqual(result["exit_code"], 127)
        self.assertIn("not found", result["stderr"])

    def test_execute_failure_raises_runtime_error(self):
        client = MagicMock()
        client.exec_command.side_effect = Exception("channel closed")
        with self.assertRaises(RuntimeError) as ctx:
            execute_command(client, "ls")
        self.assertEqual(str(ctx.exception), EXECUTE_ERROR_MESSAGE)

    # ---------- 老服务器兼容：channel 失败退避重试 ----------

    def _stdout_for(self, data: bytes, exit_code: int = 0) -> tuple:
        """构造一个能 reading 的 (stdin, stdout, stderr) 三元组。"""
        stdout = MagicMock()
        stdout.read.return_value = data
        stdout.channel.recv_exit_status.return_value = exit_code
        stderr = MagicMock()
        stderr.read.return_value = b""
        return (MagicMock(), stdout, stderr)

    @patch("core.ssh.time.sleep")  # 跳过实际 sleep 让测试秒过
    def test_retry_recovers_after_ssh_exception(self, mock_sleep):
        """首次 SSHException → 退避重试 → 第二次成功（老服务器典型行为）。"""
        import paramiko
        client = MagicMock()
        client.exec_command.side_effect = [
            paramiko.SSHException("Timeout opening channel"),
            self._stdout_for(b"recovered\n", exit_code=0),
        ]
        result = execute_command(client, "ss -tln")
        self.assertEqual(result["stdout"], "recovered\n")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(client.exec_command.call_count, 2)
        mock_sleep.assert_called_once_with(0.25)  # 第 1 次失败 → 250ms 退避

    @patch("core.ssh.time.sleep")
    def test_retry_3_times_all_fail(self, mock_sleep):
        """3 次都 SSHException → 抛 RuntimeError，sleep 调 2 次（1/2 之后才 retry）。"""
        import paramiko
        client = MagicMock()
        client.exec_command.side_effect = paramiko.SSHException("Timeout opening channel")
        with self.assertRaises(RuntimeError):
            execute_command(client, "ss -tln")
        self.assertEqual(client.exec_command.call_count, 3)
        # 第 1 次失败 → sleep(0.25)；第 2 次失败 → sleep(1.0)；第 3 次失败直接抛
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("core.ssh.time.sleep")
    def test_retry_handles_oserror(self, mock_sleep):
        """OSError（含 ConnectionResetError / BrokenPipeError）也走重试。"""
        client = MagicMock()
        client.exec_command.side_effect = [
            ConnectionResetError("connection reset by peer"),
            self._stdout_for(b"recovered\n"),
        ]
        result = execute_command(client, "ls")
        self.assertEqual(result["stdout"], "recovered\n")
        self.assertEqual(client.exec_command.call_count, 2)

    @patch("core.ssh.time.sleep")
    def test_retry_handles_socket_timeout(self, mock_sleep):
        """socket.timeout 也走重试。"""
        import socket
        client = MagicMock()
        client.exec_command.side_effect = [
            socket.timeout("read timed out"),
            self._stdout_for(b"ok\n"),
        ]
        result = execute_command(client, "ls")
        self.assertEqual(result["stdout"], "ok\n")

    @patch("core.ssh.time.sleep")
    def test_non_retriable_exception_short_circuits(self, mock_sleep):
        """ValueError 这种非通信类异常不该重试，立刻抛 RuntimeError。"""
        client = MagicMock()
        client.exec_command.side_effect = ValueError("bad command")
        with self.assertRaises(RuntimeError):
            execute_command(client, "ls")
        # 只调一次，不 retry
        self.assertEqual(client.exec_command.call_count, 1)
        mock_sleep.assert_not_called()


class TestGetSystemInfoMocked(unittest.TestCase):
    def _make_client_with_outputs(self, outputs: dict):
        """outputs: {command: (stdout_bytes, stderr_bytes, exit_code)}"""
        client = MagicMock()

        def fake_exec(cmd, timeout=30):
            stdout_data, stderr_data, exit_code = outputs.get(cmd, (b"", b"", 1))
            stdout = MagicMock()
            stdout.read.return_value = stdout_data
            stdout.channel.recv_exit_status.return_value = exit_code
            stderr = MagicMock()
            stderr.read.return_value = stderr_data
            return (MagicMock(), stdout, stderr)

        client.exec_command.side_effect = fake_exec
        return client

    def test_parses_username_and_ubuntu(self):
        os_release = (
            b'NAME="Ubuntu"\n'
            b'VERSION="22.04.3 LTS (Jammy Jellyfish)"\n'
            b"ID=ubuntu\n"
            b'VERSION_ID="22.04"\n'
        )
        client = self._make_client_with_outputs({
            "whoami": (b"root\n", b"", 0),
            "cat /etc/os-release": (os_release, b"", 0),
        })

        info = get_system_info(client)
        self.assertEqual(info, {
            "username": "root",
            "os_name": "Ubuntu",
            "os_version": "22.04",
        })

    def test_falls_back_to_version_when_no_version_id(self):
        os_release = b'NAME="Debian GNU/Linux"\nVERSION="12 (bookworm)"\n'
        client = self._make_client_with_outputs({
            "whoami": (b"admin\n", b"", 0),
            "cat /etc/os-release": (os_release, b"", 0),
        })

        info = get_system_info(client)
        self.assertEqual(info["os_name"], "Debian GNU/Linux")
        self.assertEqual(info["os_version"], "12 (bookworm)")

    def test_empty_strings_when_commands_fail(self):
        client = self._make_client_with_outputs({
            "whoami": (b"", b"err", 1),
            "cat /etc/os-release": (b"", b"no file", 1),
        })
        info = get_system_info(client)
        self.assertEqual(info, {"username": "", "os_name": "", "os_version": ""})


class TestSftpMocked(unittest.TestCase):
    """upload_file / download_file 的单元测试。"""

    def _client_with_sftp(self):
        client = MagicMock()
        sftp = MagicMock()
        client.open_sftp.return_value.__enter__.return_value = sftp
        return client, sftp

    def test_upload_calls_sftp_put(self):
        client, sftp = self._client_with_sftp()
        upload_file(client, "/local/a.txt", "/remote/a.txt")
        sftp.put.assert_called_once_with("/local/a.txt", "/remote/a.txt")

    def test_upload_failure_raises_unified_error(self):
        client = MagicMock()
        client.open_sftp.side_effect = Exception("connection lost")
        with self.assertRaises(RuntimeError) as ctx:
            upload_file(client, "/x", "/y")
        self.assertIn(FILE_TRANSFER_ERROR_MESSAGE, str(ctx.exception))

    def test_download_calls_sftp_get(self):
        client, sftp = self._client_with_sftp()
        download_file(client, "/remote/b.txt", "/local/b.txt")
        sftp.get.assert_called_once_with("/remote/b.txt", "/local/b.txt")

    def test_download_failure_raises_unified_error(self):
        client = MagicMock()
        client.open_sftp.side_effect = Exception("permission denied")
        with self.assertRaises(RuntimeError) as ctx:
            download_file(client, "/x", "/y")
        self.assertIn(FILE_TRANSFER_ERROR_MESSAGE, str(ctx.exception))


@unittest.skipUnless(
    os.environ.get("VPS_TEST_IP")
    and os.environ.get("VPS_TEST_USER")
    and os.environ.get("VPS_TEST_PASSWORD"),
    "未配置真实服务器环境变量，跳过实测",
)
class TestRealServer(unittest.TestCase):
    """真实服务器测试：连接 → 执行命令 → 关闭，全链路。"""

    @classmethod
    def setUpClass(cls):
        cls.ip = os.environ["VPS_TEST_IP"]
        cls.user = os.environ["VPS_TEST_USER"]
        cls.password = os.environ["VPS_TEST_PASSWORD"]
        cls.port = int(os.environ.get("VPS_TEST_PORT", "22"))

    def test_full_lifecycle(self):
        client = connect_server(self.ip, self.user, self.password, self.port)
        try:
            self.assertIsInstance(client, paramiko.SSHClient)

            result = execute_command(client, "echo hello_vps")
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("hello_vps", result["stdout"])

            result = execute_command(client, "whoami")
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"].strip(), self.user)

            result = execute_command(client, "this_command_does_not_exist_xyz")
            self.assertNotEqual(result["exit_code"], 0)
            self.assertTrue(result["stderr"])
        finally:
            close_server(client)

    def test_real_get_system_info(self):
        client = connect_server(self.ip, self.user, self.password, self.port)
        try:
            info = get_system_info(client)
            self.assertEqual(info["username"], self.user)
            self.assertTrue(info["os_name"], f"未取到 os_name: {info}")
            self.assertTrue(info["os_version"], f"未取到 os_version: {info}")
            print(f"\n[真实服务器系统信息] {info}")
        finally:
            close_server(client)

    def test_close_is_idempotent(self):
        client = connect_server(self.ip, self.user, self.password, self.port)
        close_server(client)
        close_server(client)

    def test_wrong_password_raises_auth_failed(self):
        """真实服务器错密码应该精确识别为 AuthFailedError 而不是泛 ConnectionError。"""
        with self.assertRaises(AuthFailedError) as ctx:
            connect_server(self.ip, self.user, "definitely_wrong_xxx", self.port)
        self.assertEqual(str(ctx.exception), AUTH_FAILED_MESSAGE)
        # 仍可被 ConnectionError 兜底
        self.assertIsInstance(ctx.exception, ConnectionError)

    def test_real_sftp_upload_and_download(self):
        """真实 SFTP：上传一个临时文件、读回内容、下载到本地验证一致。"""
        import tempfile
        client = connect_server(self.ip, self.user, self.password, self.port)
        try:
            payload = b"sftp_roundtrip_test\n"
            remote_path = "/tmp/_vps_server_sftp_test.txt"

            # 上传
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(payload)
                local_upload = f.name
            upload_file(client, local_upload, remote_path)

            # 远端验证内容
            result = execute_command(client, f"cat {remote_path}")
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["stdout"].encode(), payload)

            # 下载回本地验证
            with tempfile.NamedTemporaryFile(delete=False) as f:
                local_download = f.name
            download_file(client, remote_path, local_download)
            with open(local_download, "rb") as f:
                self.assertEqual(f.read(), payload)

            # 清理远端
            execute_command(client, f"rm -f {remote_path}")
        finally:
            close_server(client)


if __name__ == "__main__":
    unittest.main()
