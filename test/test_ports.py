"""core.ports.get_used_ports 单元测试。

mock execute_command 喂各种 ss -tln 输出，验证解析 + 区间过滤。
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.ports import (
    COMMON_RESERVED_PORTS,
    PORT_PROBE_FAILED_MESSAGE,
    PortProbeError,
    compute_available_ports,
    get_used_ports,
    is_port_free,
)


# 真实 ss -tln 输出片段（基于 Debian 12 实测样本）
SS_OUTPUT_TYPICAL = """State   Recv-Q  Send-Q  Local Address:Port   Peer Address:Port  Process
LISTEN  0       128           0.0.0.0:22            0.0.0.0:*
LISTEN  0       128         127.0.0.1:18440         0.0.0.0:*
LISTEN  0       128           0.0.0.0:18443         0.0.0.0:*
LISTEN  0       128              [::]:22               [::]:*
LISTEN  0       128             [::1]:18445            [::]:*
"""

SS_OUTPUT_EMPTY = """State   Recv-Q  Send-Q  Local Address:Port   Peer Address:Port  Process
"""

SS_OUTPUT_NO_PORTS_IN_RANGE = """State   Recv-Q  Send-Q  Local Address:Port   Peer Address:Port  Process
LISTEN  0       128           0.0.0.0:22            0.0.0.0:*
LISTEN  0       128           0.0.0.0:80            0.0.0.0:*
LISTEN  0       128           0.0.0.0:443           0.0.0.0:*
"""


class TestGetUsedPorts(unittest.TestCase):
    # ---------- 区间过滤 ----------

    @patch("core.ports.execute_command")
    def test_filters_to_range(self, mock_exec):
        """只返回 [start, end] 区间内的端口，22 / 80 等不该被算进去。"""
        mock_exec.return_value = {
            "stdout": SS_OUTPUT_TYPICAL, "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 18441, 18450)
        self.assertEqual(used, {18443, 18445})

    @patch("core.ports.execute_command")
    def test_includes_default_port_when_range_covers_it(self, mock_exec):
        """区间含 18440 时，default 端口也算占用。"""
        mock_exec.return_value = {
            "stdout": SS_OUTPUT_TYPICAL, "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 18440, 18450)
        self.assertIn(18440, used)
        self.assertIn(18443, used)
        self.assertIn(18445, used)

    @patch("core.ports.execute_command")
    def test_excludes_ports_outside_range(self, mock_exec):
        mock_exec.return_value = {
            "stdout": SS_OUTPUT_TYPICAL, "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 18441, 18450)
        self.assertNotIn(22, used)
        self.assertNotIn(18440, used)  # 不在 18441..18450

    @patch("core.ports.execute_command")
    def test_no_ports_in_range_returns_empty_set(self, mock_exec):
        mock_exec.return_value = {
            "stdout": SS_OUTPUT_NO_PORTS_IN_RANGE, "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 18441, 18450)
        self.assertEqual(used, set())

    # ---------- 地址格式 ----------

    @patch("core.ports.execute_command")
    def test_handles_ipv4_addresses(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "State Recv Send Local Address:Port Peer Process\n"
                      "LISTEN 0 128 0.0.0.0:8080 0.0.0.0:*\n",
            "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 8000, 9000)
        self.assertEqual(used, {8080})

    @patch("core.ports.execute_command")
    def test_handles_ipv6_addresses(self, mock_exec):
        """IPv6 地址形如 [::]:8080 / [::1]:8080，rsplit 必须正确取最后一段。"""
        mock_exec.return_value = {
            "stdout": "State Recv Send Local Address:Port Peer Process\n"
                      "LISTEN 0 128 [::]:8080 [::]:*\n"
                      "LISTEN 0 128 [::1]:8090 [::1]:*\n",
            "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 8000, 9000)
        self.assertEqual(used, {8080, 8090})

    @patch("core.ports.execute_command")
    def test_handles_wildcard_address(self, mock_exec):
        """通配地址 *:port 也能解析。"""
        mock_exec.return_value = {
            "stdout": "State Recv Send Local Address:Port Peer Process\n"
                      "LISTEN 0 128 *:8080 *:*\n",
            "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 8000, 9000)
        self.assertEqual(used, {8080})

    # ---------- 表头 / 空输入 ----------

    @patch("core.ports.execute_command")
    def test_skips_header_row(self, mock_exec):
        """ss -tln 第一行是表头，必须跳过免得把 'Address:Port' 也尝试解析。"""
        mock_exec.return_value = {
            "stdout": SS_OUTPUT_TYPICAL, "stderr": "", "exit_code": 0,
        }
        # 没抛异常即过（如果误把 "Address:Port" 当成端口会 ValueError）
        get_used_ports(MagicMock(), 1, 65535)

    @patch("core.ports.execute_command")
    def test_empty_output_returns_empty_set(self, mock_exec):
        mock_exec.return_value = {
            "stdout": SS_OUTPUT_EMPTY, "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 18441, 18450)
        self.assertEqual(used, set())

    @patch("core.ports.execute_command")
    def test_malformed_line_is_skipped_not_raises(self, mock_exec):
        """中间一行解析失败不该爆掉整个函数。"""
        mock_exec.return_value = {
            "stdout": "State Recv Send Local Address:Port Peer Process\n"
                      "LISTEN 0 128 0.0.0.0:18443 0.0.0.0:*\n"
                      "garbage line that won't parse\n"
                      "LISTEN 0 128 0.0.0.0:18445 0.0.0.0:*\n",
            "stderr": "", "exit_code": 0,
        }
        used = get_used_ports(MagicMock(), 18441, 18450)
        self.assertEqual(used, {18443, 18445})

    # ---------- 失败路径 ----------

    @patch("core.ports.execute_command")
    def test_command_failure_raises_port_probe_error(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "", "stderr": "ss: command not found", "exit_code": 127,
        }
        with self.assertRaises(PortProbeError) as ctx:
            get_used_ports(MagicMock(), 18441, 18450)
        self.assertIn(PORT_PROBE_FAILED_MESSAGE, str(ctx.exception))
        self.assertIn("ss: command not found", str(ctx.exception))

    @patch("core.ports.execute_command")
    def test_uses_ss_tln_command(self, mock_exec):
        """实测下来的命令必须是 `ss -tln 2>/dev/null`：
        -t TCP only, -l listening only, -n numeric。
        """
        mock_exec.return_value = {"stdout": "State\n", "stderr": "", "exit_code": 0}
        get_used_ports(MagicMock(), 1, 65535)
        args, _ = mock_exec.call_args
        self.assertIn("ss -tln", args[1])


# ============================================================
# is_port_free：单端口便捷查询
# ============================================================

class TestIsPortFree(unittest.TestCase):
    @patch("core.ports.execute_command")
    def test_returns_true_when_port_not_listening(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "State Recv Send Local Address:Port Peer Process\n"
                      "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n",
            "stderr": "", "exit_code": 0,
        }
        self.assertTrue(is_port_free(MagicMock(), 18443))

    @patch("core.ports.execute_command")
    def test_returns_false_when_port_in_use(self, mock_exec):
        mock_exec.return_value = {
            "stdout": "State Recv Send Local Address:Port Peer Process\n"
                      "LISTEN 0 128 0.0.0.0:18443 0.0.0.0:*\n",
            "stderr": "", "exit_code": 0,
        }
        self.assertFalse(is_port_free(MagicMock(), 18443))


# ============================================================
# compute_available_ports：纯函数算可用集合
# ============================================================

class TestComputeAvailablePorts(unittest.TestCase):
    def test_returns_all_when_no_used_no_exclude(self):
        free = compute_available_ports(used=set(), start_port=10, end_port=12, exclude=set())
        self.assertEqual(free, {10, 11, 12})

    def test_subtracts_used(self):
        free = compute_available_ports(used={11}, start_port=10, end_port=12, exclude=set())
        self.assertEqual(free, {10, 12})

    def test_subtracts_exclude(self):
        free = compute_available_ports(used=set(), start_port=10, end_port=12, exclude={11})
        self.assertEqual(free, {10, 12})

    def test_subtracts_both_used_and_exclude(self):
        free = compute_available_ports(
            used={10}, start_port=10, end_port=12, exclude={12},
        )
        self.assertEqual(free, {11})

    def test_exclude_outside_range_is_no_op(self):
        """exclude 列表里有不在区间的端口不影响结果。"""
        free = compute_available_ports(
            used=set(), start_port=10, end_port=12, exclude={22, 443, 11},
        )
        self.assertEqual(free, {10, 12})

    def test_default_exclude_is_common_reserved(self):
        """不传 exclude 时默认走 COMMON_RESERVED_PORTS，22/443 等会被排除。"""
        # 区间含 22 (SSH)，确认它被默认排除
        free = compute_available_ports(used=set(), start_port=20, end_port=25)
        self.assertNotIn(22, free)  # 22 在 COMMON_RESERVED_PORTS 里
        self.assertIn(20, free)
        self.assertIn(21, free)
        self.assertIn(23, free)

    def test_empty_range_returns_empty(self):
        """start > end 时返回空集合（边界）。"""
        free = compute_available_ports(used=set(), start_port=20, end_port=19, exclude=set())
        self.assertEqual(free, set())

    def test_single_port_range(self):
        free = compute_available_ports(used=set(), start_port=18443, end_port=18443, exclude=set())
        self.assertEqual(free, {18443})

    def test_realistic_proxy_range_scenario(self):
        """模拟 rgIP 真实场景：区间 18441-18450，扣掉 OS 占用 + 默认排除。"""
        used_in_range = {18445}  # ss -tln 扫到 18445 被占
        free = compute_available_ports(
            used=used_in_range, start_port=18441, end_port=18450,
        )
        # 18445 已用 → 排除；COMMON_RESERVED_PORTS 里没有 18441-18450 任一个
        # 所以可用应该是 {18441, 18442, 18443, 18444, 18446, 18447, 18448, 18449, 18450}
        self.assertEqual(
            free,
            {18441, 18442, 18443, 18444, 18446, 18447, 18448, 18449, 18450},
        )


# ============================================================
# COMMON_RESERVED_PORTS：常量自检
# ============================================================

class TestCommonReservedPorts(unittest.TestCase):
    def test_is_frozenset(self):
        """常量应该是 frozenset，业务里直接用不能被改。"""
        self.assertIsInstance(COMMON_RESERVED_PORTS, frozenset)

    def test_includes_common_service_ports(self):
        """22/80/443/3306 必须在内（这是 RCE 攻击者爱挑的端口，业务永远不该用）。"""
        for port in (22, 80, 443, 3306):
            self.assertIn(port, COMMON_RESERVED_PORTS, f"{port} 不在 COMMON_RESERVED_PORTS")


if __name__ == "__main__":
    unittest.main()
