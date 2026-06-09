"""TC-01 ProxyStatus 3 档枚举 + MAX_PORTS_PER_VPS + EXCLUDED_PORTS 复用核查。

对应任务: task/doing_15_proxy_deploy_prereq.md
对应 ADR: docs/adr/0006-proxy-deploy-worker.md §决策 §3 §6 §7
对应 spec: test/proxy_deploy_worker/spec.md §5 §6
"""

from __future__ import annotations

import unittest


class TestProxyStatusEnum(unittest.TestCase):
    """ProxyStatus 3 档枚举（ADR-0006 §7）。"""

    def test_3_status_values(self) -> None:
        from db.models import ProxyStatus

        self.assertEqual(ProxyStatus.USING, "using")
        self.assertEqual(ProxyStatus.PENDING_FW, "pending_fw")
        self.assertEqual(ProxyStatus.INACTIVE, "inactive")

    def test_old_expired_removed(self) -> None:
        """旧 EXPIRED 已被 INACTIVE 吸收, 不应再存在（防回退）。"""
        from db.models import ProxyStatus

        self.assertFalse(
            hasattr(ProxyStatus, "EXPIRED"),
            "ProxyStatus.EXPIRED 应已改名 INACTIVE (ADR-0006 §7)",
        )

    def test_default_still_using(self) -> None:
        """新建 ProxyRecord 默认 status 仍是 USING（ProxyDeployWorker 成功路径默认值）。"""
        from db.models import ProxyRecord, ProxyStatus

        col = ProxyRecord.__table__.c.status
        # SQLAlchemy default 是 ColumnDefault 包装, 取 .arg
        self.assertEqual(col.default.arg, ProxyStatus.USING)


class TestMaxPortsPerVps(unittest.TestCase):
    """config.MAX_PORTS_PER_VPS 常量（ADR-0006 §3）。"""

    def test_constant_exists_and_value(self) -> None:
        from config import MAX_PORTS_PER_VPS

        self.assertEqual(MAX_PORTS_PER_VPS, 3)
        self.assertIsInstance(MAX_PORTS_PER_VPS, int)


class TestExcludedPortsReuse(unittest.TestCase):
    """EXCLUDED_PORTS 概念复用 toolbox.ports.COMMON_RESERVED_PORTS, 不双轨。"""

    def test_common_reserved_ports_covers_high_freq_apps(self) -> None:
        """常见应用端口必须在排除清单内（防 ProxyDeployWorker 撞到生产服务）。"""
        from toolbox.ports import COMMON_RESERVED_PORTS

        # well-known（防御性留, 即使 start_port>=1024 也兜底）
        for port in (22, 25, 53, 80, 443):
            self.assertIn(port, COMMON_RESERVED_PORTS, f"port {port} 应在排除清单")

        # 1024+ 常见应用服务（T-15 补全的）
        for port in (1080, 3306, 5432, 6379, 8080, 9090, 9100, 11211, 27017):
            self.assertIn(port, COMMON_RESERVED_PORTS, f"port {port} 应在排除清单")

        # 项目历史保留
        self.assertIn(18789, COMMON_RESERVED_PORTS)

    def test_no_excluded_ports_in_config(self) -> None:
        """config.py 不允许另起 EXCLUDED_PORTS, 避免跟 COMMON_RESERVED_PORTS 双轨。"""
        import config

        self.assertFalse(
            hasattr(config, "EXCLUDED_PORTS"),
            "不允许 config.EXCLUDED_PORTS, 跟 toolbox.ports.COMMON_RESERVED_PORTS 双轨 "
            "(CLAUDE.local.md §反模式)",
        )


if __name__ == "__main__":
    unittest.main()
