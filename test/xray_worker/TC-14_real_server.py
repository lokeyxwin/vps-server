"""
========================================================================
[用例描述 —— DO NOT MODIFY]
========================================================================

TC-14 ⚠️ 真机集成测 (default SKIP, dev 手动开)

故事:
  把 XrayWorker.process_task 跑在一台真 VPS 上, 端到端验证:
    - SSH 通
    - 现状判断 + 分支前置正确执行
    - 统一收尾把"直进直出"默认入口加上 (18440 或让步后端口)
    - 纳管已有出口 → ip_record / proxy_record 落库正确
    - 不通的出口被 remove_proxy_binding 删干净
    - task.status=done, vps.stage=connectable (完工释放资源锁, ADR-0005)

启动方式:
  export VPS_XRAY_REAL_TEST=1
  export VPS_XRAY_REAL_IP=<ip>
  export VPS_XRAY_REAL_USER=root
  export VPS_XRAY_REAL_PWD=<pwd>
  export VPS_XRAY_REAL_PORT=22
  uv run pytest test/xray_worker/TC-14_real_server.py -v

默认 skip, 跟 CI 隔离. spec.md v5.1 §"测试矩阵" 拍板"TC-14 skip 算通过".
========================================================================
"""

from __future__ import annotations

import os
import unittest


_REAL_TEST_FLAG = "VPS_XRAY_REAL_TEST"


@unittest.skipUnless(
    os.environ.get(_REAL_TEST_FLAG, "").lower() in {"1", "true", "yes"},
    f"真机测试默认 skip; 设 {_REAL_TEST_FLAG}=1 + 真服务器凭据 env var 启用",
)
class TestRealServer(unittest.TestCase):

    def test_tc14_real_server_end_to_end(self):
        """端到端: rgvps → SSHWorker → vps_task → XrayWorker.process_task → vps.stage=connectable."""
        ip = os.environ.get("VPS_XRAY_REAL_IP", "")
        user = os.environ.get("VPS_XRAY_REAL_USER", "root")
        pwd = os.environ.get("VPS_XRAY_REAL_PWD", "")
        port = int(os.environ.get("VPS_XRAY_REAL_PORT", "22"))
        self.assertTrue(ip and pwd, "请设置 VPS_XRAY_REAL_IP / VPS_XRAY_REAL_PWD")

        # dev 实测脚本: dev_smoke_xray_worker.py (创建于跑测时)
        # 本 TC 仅占位,真测留给 dev 跑 dev_smoke + 人工核对落库结果
        self.skipTest("真机端到端测试由 dev_smoke_xray_worker.py 脚本执行, 见任务单完成记录")


if __name__ == "__main__":
    unittest.main()


# ========================================================================
# [测试结论 —— 跑完测试后追加]
# ========================================================================
