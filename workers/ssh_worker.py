"""SSHWorker —— 敲门工(rgvps MCP 工具的同步段).

干啥:
  用户提交一台服务器(ip/账号/密码/端口),我去敲门 + 顺手看一眼 + 入库 +
  派下一活儿(install_xray task).几秒内完成.

谁会调我:
  tools/rgvps.py (MCP 工具入口)

我会用到的工具:
  - ssh.session.VPSSession (SSH 通话手柄类,有状态)
  - xray.manager.XrayManager.version (只这一个方法,看 xray 装没装)
  - db.models (VPSRecord / VPSTask 等)

我的私有编排方法(下划线开头):
  - _查重 _敲门看一眼 _入库派任务 _失败路径处理

我返回的 status 集:
  - already_registered (DB 已有这台)
  - queued            (新登记 + 入库 + 派任务成功)
  - auth_failed       (密码错,不入库)
  - unreachable       (超时/拒接,重试仍失败,入库标 unreachable)

行为规约金标准:
  tests_behavior/ssh_worker/spec.md

实现等任务单填,本文件目前是骨架占位.
"""

from __future__ import annotations


class SSHWorker:
    """敲门工.调用方:tools/rgvps.py 的 handler."""

    def __init__(self) -> None:
        # 工人出生时不绑 client / task,每次 process 来一条新的
        pass

    # ============ 主入口(MCP handler 调这个) ============

    def process(
        self,
        ip: str,
        user: str,
        pwd: str,
        port: int,
        ed=None,
        provider: str = "",
    ) -> dict:
        """敲门 + 入库 + 派活儿,主流程.

        见 spec.md §3 三条主路线.
        返回 dict 含 status + 其他业务字段.
        """
        pass    # 实现等任务单填

    # ============ 工人私有的小工具(下划线开头) ============

    def _查重(self, ip: str):
        """看 vps_record 表有没有这个 ip.

        命中 → 返回打包好的现状 dict(含关联活跃 task).
        没命中 → 返回 None.
        """
        pass

    def _敲门看一眼(self, ip: str, user: str, pwd: str, port: int) -> dict:
        """SSH 探测 + 顺手采集 OS / xray 版本.

        见 spec.md §3 路线 B ② ③.
        返回 dict 含: ok / client / os_name / os_version / xray_version / error.
        失败时 ok=False, error 内容含 auth_failed / timeout / refused / failed.
        """
        pass

    def _入库派任务(
        self,
        ip: str,
        user: str,
        pwd: str,
        port: int,
        ed,
        provider: str,
        os_name: str,
        os_version: str,
        xray_version: str,
    ) -> int:
        """写 vps_record(stage=connectable) + vps_task(install_xray, pending).

        返回 task_id.
        见 spec.md §3 路线 B ④.
        """
        pass

    def _失败路径处理(
        self,
        ip: str,
        user: str,
        pwd: str,
        port: int,
        ed,
        provider: str,
        error: str,
    ) -> dict:
        """SSH 失败时分两种处理.

        - auth_failed → 不入库,直接返回错误
        - timeout / refused → 内部重试 N 次(参数走 config.py)仍失败
          → 入库 stage=unreachable + 提示语
        见 spec.md §3 路线 C.
        """
        pass
