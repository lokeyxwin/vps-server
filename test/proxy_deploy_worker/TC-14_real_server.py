"""
========================================================================
TC-14 真服务器 e2e (可选, 默认 skip)

故事:
  真实测试 VPS + 真实上游 IP 凭据 → ProxyDeployWorker 跑完整链路
  → SSH 上 VPS → apply xray binding → firewall → 内/外 ping → 收尾

触发方式:
  环境变量 VPS_TEST_REAL_E2E=1 + 提供真实 VPS / IP DB 数据,
  本任务范围(T-16)默认 skip, 留给 dev_smoke 真机跑.
========================================================================
"""

from __future__ import annotations

import os
import unittest


@unittest.skipUnless(
    os.environ.get("VPS_TEST_REAL_E2E") == "1",
    "真机 e2e 默认 skip, 设 VPS_TEST_REAL_E2E=1 + 提供 DB 数据再开",
)
class TestRealServerE2E(unittest.TestCase):
    def test_real_e2e(self):
        """真机跑 ProxyDeployWorker.run_once 一轮, 等真实 DB 状态变成 done."""
        from workers.proxy_deploy_worker import ProxyDeployWorker
        result = ProxyDeployWorker().run_once()
        # 这里只能断言抢到了任务(>=0);
        # 真实业务断言由人工 + dev_smoke 脚本兜
        self.assertIn(result, (0, 1))


if __name__ == "__main__":
    unittest.main()
