"""VPSSession 端口探测方法的单元测试。

测策略：mock 底层 core.ports 函数，验证 VPSSession 方法只是薄包装且
正确传递了 self._client。
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.session import VPSSession


def _make_connected_session() -> VPSSession:
    """构造一个已"伪连接"的 VPSSession（绕过真实 SSH）。"""
    s = VPSSession("1.2.3.4", "root", "pwd", 22)
    s._client = MagicMock()  # 伪装已连接
    return s


class TestSessionPortMethods(unittest.TestCase):
    # ---------- is_port_free ----------

    @patch("core.session.is_port_free")
    def test_is_port_free_delegates_with_client(self, mock_fn):
        mock_fn.return_value = True
        s = _make_connected_session()
        result = s.is_port_free(18443)
        self.assertTrue(result)
        mock_fn.assert_called_once_with(s._client, 18443)

    def test_is_port_free_raises_when_not_connected(self):
        s = VPSSession("1.2.3.4", "root", "pwd", 22)  # 没 connect
        with self.assertRaises(RuntimeError):
            s.is_port_free(18443)

    # ---------- get_used_ports ----------

    @patch("core.session.get_used_ports")
    def test_get_used_ports_delegates_with_client(self, mock_fn):
        mock_fn.return_value = {18443, 18445}
        s = _make_connected_session()
        result = s.get_used_ports(18441, 18450)
        self.assertEqual(result, {18443, 18445})
        mock_fn.assert_called_once_with(s._client, 18441, 18450)

    def test_get_used_ports_raises_when_not_connected(self):
        s = VPSSession("1.2.3.4", "root", "pwd", 22)
        with self.assertRaises(RuntimeError):
            s.get_used_ports(18441, 18450)

    # ---------- get_available_ports ----------

    @patch("core.session.compute_available_ports")
    @patch("core.session.get_used_ports")
    def test_get_available_combines_used_and_compute(self, mock_used, mock_compute):
        """get_available_ports = get_used_ports + compute_available_ports 的编排。"""
        mock_used.return_value = {18445}
        mock_compute.return_value = {18441, 18442, 18443, 18444}

        s = _make_connected_session()
        result = s.get_available_ports(18441, 18445)

        # 验证编排：先调 get_used_ports，再用结果调 compute_available_ports
        mock_used.assert_called_once_with(s._client, 18441, 18445)
        mock_compute.assert_called_once_with({18445}, 18441, 18445, None)
        self.assertEqual(result, {18441, 18442, 18443, 18444})

    @patch("core.session.compute_available_ports")
    @patch("core.session.get_used_ports")
    def test_get_available_passes_custom_exclude(self, mock_used, mock_compute):
        """传入自定义 exclude 时应原样透传到 compute_available_ports。"""
        mock_used.return_value = set()
        mock_compute.return_value = set()

        s = _make_connected_session()
        custom_exclude = {18444, 18445}
        s.get_available_ports(18441, 18450, exclude=custom_exclude)

        mock_compute.assert_called_once_with(set(), 18441, 18450, custom_exclude)

    def test_get_available_raises_when_not_connected(self):
        s = VPSSession("1.2.3.4", "root", "pwd", 22)
        with self.assertRaises(RuntimeError):
            s.get_available_ports(18441, 18450)


if __name__ == "__main__":
    unittest.main()
