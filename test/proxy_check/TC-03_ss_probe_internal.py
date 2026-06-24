"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-03 ShadowsocksProbe.test_internal —— 临时 xray 端到端 (ADR-0011 §决策 §5)

故事:
  内 ping 在目标 VPS 本机起临时 xray(socks-in → ss-out 连本机 SS 端口),
  curl --socks5 临时口 拿出口 IP。完整验证 SS 握手+加密+密码+上游出口。
  ⚠️ try/finally 兜底 kill 临时进程 + 删临时配置, 绝不碰主 xray config。

  本类全程走 ssh.ops.execute_command, 测试 mock 它(按调用顺序给返回):
    call#1 写临时配置  call#2 起 xray  call#3 curl  call#4 cleanup(kill+rm)

测试矩阵:
  TC-03-a 全通       → (True, egress); 临时配置写到 /tmp/_ss_probe.json(不碰主 config)
  TC-03-b curl 不通  → (False, ""); cleanup 仍执行
  TC-03-c 起 xray 失败(kill -0 探活到进程已死, exit!=0) → (False, ""); cleanup 仍执行(finally)
  TC-03-d 写配置失败  → (False, ""); cleanup 仍执行(finally)
  TC-03-e 临时配置内容 = socks-in(noauth) → ss-out(method/password 连 127.0.0.1:port);
          写入走 base64(echo <b64> | base64 -d), 不走 heredoc
  TC-03-f cleanup 命令含 kill pid + rm 临时文件
  TC-03-g cleanup 自身抛错不影响探测结果(finally 内已兜底)
  TC-03-h _start_probe_xray 命令含 kill -0 存活检查(后台化检测不到死进程的修复)
  TC-03-i curl 用 --socks5-hostname(远端 DNS 解析, 对齐 socks5h 语义)
========================================================================
"""

from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from toolbox.proxy_check import (
    _SS_PROBE_CONFIG_PATH,
    _SS_PROBE_PID_PATH,
    ShadowsocksProbe,
)


def _ok_result(stdout: str = "", exit_code: int = 0) -> dict:
    return {"stdout": stdout, "stderr": "", "exit_code": exit_code}


def _decode_write_config(write_cmd: str) -> dict:
    """从 `echo <b64> | base64 -d > path` 命令里抠出 b64 token 解码回 config dict。"""
    token = write_cmd.split("echo", 1)[1].split("|", 1)[0].strip()
    return json.loads(base64.b64decode(token).decode("utf-8"))


_CURL_OK_STDOUT = "__HTTPCODE__200__BODY__\n198.51.100.42"
_CURL_FAIL_STDOUT = "__HTTPCODE__000__BODY__\n"


class TestShadowsocksProbeInternal(unittest.TestCase):
    def setUp(self):
        self.probe = ShadowsocksProbe()
        self.client = MagicMock(name="ssh_client")

    # ---------- TC-03-a ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03a_all_ok_returns_egress(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(),                       # write config
            _ok_result(),                       # start xray
            _ok_result(_CURL_OK_STDOUT),        # curl
            _ok_result(),                       # cleanup
        ]
        ok, egress = self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        self.assertTrue(ok)
        self.assertEqual(egress, "198.51.100.42")
        # 临时配置写到独立路径, 命令绝不触碰主 config 路径
        write_cmd = mock_exec.call_args_list[0].args[1]
        self.assertIn(_SS_PROBE_CONFIG_PATH, write_cmd)
        all_cmds = " ".join(c.args[1] for c in mock_exec.call_args_list)
        self.assertNotIn("/usr/local/etc/xray/config.json", all_cmds)

    # ---------- TC-03-b ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03b_curl_fail_returns_empty_and_cleans(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(),
            _ok_result(),
            _ok_result(_CURL_FAIL_STDOUT),      # curl 不通
            _ok_result(),
        ]
        ok, egress = self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        self.assertFalse(ok)
        self.assertEqual(egress, "")
        # 第 4 次调用 = cleanup, 必被执行
        self.assertEqual(mock_exec.call_count, 4)
        self._assert_last_is_cleanup(mock_exec)

    # ---------- TC-03-c ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03c_start_fail_still_cleans(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(),                       # write ok
            _ok_result(exit_code=1),            # start: kill -0 探活到进程已死 → exit!=0 → 抛 → finally
            _ok_result(),                       # cleanup
        ]
        ok, egress = self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        self.assertFalse(ok)
        self.assertEqual(egress, "")
        self._assert_last_is_cleanup(mock_exec)

    # ---------- TC-03-d ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03d_write_fail_still_cleans(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(exit_code=1),            # write 失败 → 抛 → finally
            _ok_result(),                       # cleanup
        ]
        ok, egress = self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        self.assertFalse(ok)
        self.assertEqual(egress, "")
        self._assert_last_is_cleanup(mock_exec)

    # ---------- TC-03-e ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03e_probe_config_shape(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(), _ok_result(), _ok_result(_CURL_OK_STDOUT), _ok_result(),
        ]
        self.probe.test_internal(
            self.client, 18450, method="chacha20-poly1305", password="topsecret",
        )
        write_cmd = mock_exec.call_args_list[0].args[1]
        # 写入走 base64 通道, 不走 heredoc: echo <b64> | base64 -d > path
        self.assertIn("base64 -d", write_cmd)
        self.assertNotIn("<<", write_cmd)
        cfg = _decode_write_config(write_cmd)
        inbound = cfg["inbounds"][0]
        outbound = cfg["outbounds"][0]
        self.assertEqual(inbound["protocol"], "socks")
        self.assertEqual(inbound["settings"]["auth"], "noauth")
        self.assertEqual(outbound["protocol"], "shadowsocks")
        server = outbound["settings"]["servers"][0]
        self.assertEqual(server["address"], "127.0.0.1")
        self.assertEqual(server["port"], 18450)
        self.assertEqual(server["method"], "chacha20-poly1305")
        self.assertEqual(server["password"], "topsecret")

    # ---------- TC-03-f ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03f_cleanup_cmd_kills_and_removes(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(), _ok_result(), _ok_result(_CURL_OK_STDOUT), _ok_result(),
        ]
        self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        cleanup_cmd = mock_exec.call_args_list[-1].args[1]
        self.assertIn("kill", cleanup_cmd)
        self.assertIn(_SS_PROBE_PID_PATH, cleanup_cmd)
        self.assertIn("rm -f", cleanup_cmd)
        self.assertIn(_SS_PROBE_CONFIG_PATH, cleanup_cmd)

    # ---------- TC-03-g ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03g_cleanup_error_swallowed(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(),
            _ok_result(),
            _ok_result(_CURL_OK_STDOUT),
            RuntimeError("cleanup ssh dropped"),   # cleanup 自身抛错
        ]
        # cleanup 抛错被 finally 内 try/except 吞掉, 不冒泡, 探测结果照常返回
        ok, egress = self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        self.assertTrue(ok)
        self.assertEqual(egress, "198.51.100.42")

    # ---------- TC-03-h ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03h_start_cmd_has_liveness_check(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(), _ok_result(), _ok_result(_CURL_OK_STDOUT), _ok_result(),
        ]
        self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        start_cmd = mock_exec.call_args_list[1].args[1]
        # 后台化检测不到死进程的修复: sleep 后用 kill -0 探活
        self.assertIn("kill -0", start_cmd)
        self.assertIn(_SS_PROBE_PID_PATH, start_cmd)

    # ---------- TC-03-i ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03i_curl_uses_socks5_hostname(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(), _ok_result(), _ok_result(_CURL_OK_STDOUT), _ok_result(),
        ]
        self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password="pw",
        )
        curl_cmd = mock_exec.call_args_list[2].args[1]
        self.assertIn("--socks5-hostname", curl_cmd)
        # 不再用裸 --socks5(本地 DNS 解析)
        self.assertNotIn("--socks5 ", curl_cmd)

    # ---------- TC-03-j (base64 写入对特殊字符密码安全) ----------
    @patch("toolbox.proxy_check.execute_command")
    def test_tc03j_special_char_password_survives_base64(self, mock_exec):
        mock_exec.side_effect = [
            _ok_result(), _ok_result(), _ok_result(_CURL_OK_STDOUT), _ok_result(),
        ]
        nasty = "p'w\"d`$(rm -rf /)\n; echo pwned"
        self.probe.test_internal(
            self.client, 18441, method="aes-256-gcm", password=nasty,
        )
        write_cmd = mock_exec.call_args_list[0].args[1]
        cfg = _decode_write_config(write_cmd)
        # 特殊字符/换行原样保留, 且不在 shell 命令文本里裸出现(无注入)
        self.assertEqual(cfg["outbounds"][0]["settings"]["servers"][0]["password"], nasty)
        self.assertNotIn("rm -rf", write_cmd)
        self.assertNotIn("echo pwned", write_cmd)

    # ---------- helper ----------
    def _assert_last_is_cleanup(self, mock_exec):
        cleanup_cmd = mock_exec.call_args_list[-1].args[1]
        self.assertIn("kill", cleanup_cmd)
        self.assertIn("rm -f", cleanup_cmd)


if __name__ == "__main__":
    unittest.main()
