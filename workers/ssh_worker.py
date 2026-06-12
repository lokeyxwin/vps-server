"""SSHWorker —— 敲门工(rgvps MCP 工具的同步段).

干啥:
  用户提交一台服务器(ip/账号/密码/端口),我去敲门 + 顺手看一眼 + 入库 +
  派下一活儿(install_xray task).几秒内完成.

谁会调我:
  tools/rgvps.py (MCP 工具入口)

我会用到的工具:
  - ssh.session.VPSSession (SSH 通话手柄类,有状态)
  - db.models (VPSRecord / VPSTask 等)

我不碰 xray: 不调 XrayManager / 不探 xray 版本, xray_version 永远写空,
装没装由 XrayWorker 拿到任务后实时探 `xray version` 判定。

我的私有编排方法(下划线开头):
  - _lookup_existing _probe_ssh _persist_and_dispatch _handle_failure

我返回的 status 集:
  - already_registered (DB 已有这台)
  - queued            (新登记 + 入库 + 派任务成功)
  - auth_failed       (密码错,不入库)
  - unreachable       (超时/拒接,重试仍失败,入库标 unreachable)

行为规约金标准:
  test/ssh_worker/spec.md

实现等任务单填,本文件目前是骨架占位.
"""

from __future__ import annotations

from db.models import TaskStatus, VPSRecord, VPSStage, VPSTask
from db.session import session_scope
from log import get_logger
from ssh.ops import (
    AuthFailedError,
    ConnectRefusedError,
    ConnectTimeoutError,
)
from ssh.session import VPSSession


logger = get_logger("services.ssh_worker")


# ============ 路线 C 失败提示文案 (spec v4 §3 路线 C) ============
# 用户拍板的口径:
#   - 不引导用户去防火墙作为首要排查(端口错/安全策略组才是主因)
#   - 账密错 -> 让用户核对 0/o l/I/1 + 区分服务商面板密码 vs SSH 密码

_AUTH_FAILED_MESSAGE = (
    "请核对账号密码。OCR 可能看错 0/o、l/I/1；"
    "服务商面板密码 ≠ SSH 密码。"
)


def _build_timeout_message(port: int) -> str:
    return (
        f"SSH 端口 {port} 连接超时。"
        f"可能端口错 → 服务商控制台核对远程登录端口；"
        f"端口对的话 → 安全策略组开放入方向（含 22 或远程登录端口）；"
        f"都对还不行 → 服务商面板自查。"
    )


def _build_refused_message(port: int) -> str:
    return (
        f"SSH 端口 {port} 被拒绝。"
        f"可能端口错或服务未监听该端口 → 服务商控制台核对远程登录端口；"
        f"端口对的话 → 安全策略组开放入方向。"
    )


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
        """敲门 + 入库 + 派活儿,主流程 (spec v4 §3 三条主路线 A/B/C).

        编排 4 个私有方法, 自身不直接调 VPSSession / DB / XrayManager.
        返回 dict 含 status + 其他业务字段, 5 种 status:
          - already_registered (路线 A: DB 已有, 不打 SSH)
          - queued             (路线 B: 新登记 + 入库 + 派任务成功)
          - auth_failed        (路线 C: 密码错, 不入库)
          - ssh_timeout        (路线 C: 超时, 不入库)
          - ssh_refused        (路线 C: 拒接, 不入库)
          - ssh_failed         (路线 C: 兜底, 不入库)
        """
        # ---- 路线 A: DB 已有 ----
        existing = self._lookup_existing(ip)
        if existing is not None:
            logger.info("process: ip=%s → 已登记, 路线 A 短路", ip)
            return {
                "status": "already_registered",
                "vps": existing,
            }

        # ---- DB 没有 → 探测 ----
        probe = self._probe_ssh(ip, user, pwd, port)

        if not probe["ok"]:
            # ---- 路线 C: SSH 失败 → 抛回不入库 ----
            logger.info(
                "process: ip=%s → 路线 C 失败 error_type=%s",
                ip, probe["error_type"],
            )
            return self._handle_failure(
                error_type=probe["error_type"],
                error_message=probe["error_message"],
                port=port,
            )

        # ---- 路线 B: 探测成功 → 入库 + 派任务 ----
        # v4 关键: 不传 xray_version (_persist_and_dispatch 已删此参数)
        result = self._persist_and_dispatch(
            ip=ip,
            user=user,
            pwd=pwd,
            port=port,
            ed=ed,
            provider=provider,
            os_name=probe["os_name"],
            os_version=probe["os_version"],
        )
        logger.info(
            "process: ip=%s → 路线 B 入库 vps_id=%s task_id=%s",
            ip, result["vps_id"], result["task_id"],
        )
        return {
            "status": "queued",
            "task_id": result["task_id"],
            "vps_id": result["vps_id"],
            "vps": {
                "ip": ip,
                "stage": VPSStage.CONNECTABLE,
                "xray_version": "",  # v4 §5 不变量: SSHWorker 永远写空
                "os_name": probe["os_name"],
                "os_version": probe["os_version"],
            },
            "message": (
                "已确认账密 OK,已入库;后台 worker 会接手装 xray"
            ),
        }

    # ============ 工人私有的小工具(下划线开头) ============

    @staticmethod
    def _lookup_existing(ip: str) -> dict | None:
        """看 vps_record 表有没有这个 ip.

        命中 → 返回打包好的现状 dict(含关联活跃 task 或最近 task 的 last_error_*).
        没命中 → 返回 None.

        spec v4:
          - 删 stage_message 字段(错误信息住任务表)
          - "活跃" status 集合: [PENDING, IN_PROGRESS] (v4 没有 PENDING_RETRY)
          - 即便没活跃 task, 最近一条 task 的 last_error_* 也带回(便于 agent 转告)
        """
        with session_scope() as s:
            rec = s.query(VPSRecord).filter_by(ip=ip).first()
            if rec is None:
                return None

            # 找活跃 task(v4: pending / in_progress 两值)
            active = (
                s.query(VPSTask)
                .filter(
                    VPSTask.vps_id == rec.id,
                    VPSTask.status.in_(
                        [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
                    ),
                )
                .order_by(VPSTask.created_at.desc())
                .first()
            )

            # 没有活跃 task 时, 取最近一条 task 的错误信息(可能是 failed)
            latest = None
            if active is None:
                latest = (
                    s.query(VPSTask)
                    .filter(VPSTask.vps_id == rec.id)
                    .order_by(VPSTask.created_at.desc())
                    .first()
                )

            active_task_dict = None
            if active is not None:
                active_task_dict = {
                    "task_id": active.id,
                    "status": active.status,
                    "retry_count": active.retry_count,
                    "next_run_at": (
                        active.next_run_at.isoformat()
                        if active.next_run_at is not None
                        else ""
                    ),
                    "last_error_code": active.last_error_code,
                    "last_error_msg": active.last_error_msg,
                }

            # 历史错误信息: 优先活跃 task, 没活跃就拿最近一条 (任何 status)
            history_source = active if active is not None else latest
            last_error_code = (
                history_source.last_error_code if history_source is not None else ""
            )
            last_error_msg = (
                history_source.last_error_msg if history_source is not None else ""
            )

            return {
                "vps_id": rec.id,
                "ip": rec.ip,
                "stage": rec.stage,
                "xray_version": rec.xray_version,
                "os_name": rec.os_name,
                "os_version": rec.os_version,
                "is_active": rec.is_active,
                "active_task": active_task_dict,
                "last_error_code": last_error_code,
                "last_error_msg": last_error_msg,
            }

    @staticmethod
    def _probe_ssh(ip: str, user: str, pwd: str, port: int) -> dict:
        """SSH 探测 + 顺手采集 OS (spec v4 §3 路线 B 步骤①②③).

        ⚠️ v4 不查任何 xray 信息, 绝不调用 XrayManager.
        VPSSession with 包起来用完即关.

        返回 dict:
          - ok:            bool
          - os_name:       str (拿不到留空)
          - os_version:    str (拿不到留空)
          - error_type:    str | None ('auth_failed' / 'timeout' / 'refused' / 'failed')
          - error_message: str (ok=False 时填错误描述)
        """
        try:
            with VPSSession(ip, user, pwd, port) as sess:
                # spec v4 §6: SSH 通但 get_system_info 报错 → os 留空, ok 仍为 True
                try:
                    info = sess.get_system_info()
                    os_name = info.get("os_name", "")
                    os_version = info.get("os_version", "")
                except Exception as exc:  # noqa: BLE001 — 读 OS 失败留空, 不影响 ok
                    logger.warning(
                        "_probe_ssh: ip=%s get_system_info failed (%s), os 留空",
                        ip, exc,
                    )
                    os_name = ""
                    os_version = ""
                return {
                    "ok": True,
                    "os_name": os_name,
                    "os_version": os_version,
                    "error_type": None,
                    "error_message": "",
                }
        except AuthFailedError as exc:
            logger.info("_probe_ssh: ip=%s → auth_failed", ip)
            return {
                "ok": False,
                "os_name": "",
                "os_version": "",
                "error_type": "auth_failed",
                "error_message": str(exc) or _AUTH_FAILED_MESSAGE,
            }
        except ConnectTimeoutError as exc:
            logger.info("_probe_ssh: ip=%s port=%s → timeout", ip, port)
            return {
                "ok": False,
                "os_name": "",
                "os_version": "",
                "error_type": "timeout",
                "error_message": str(exc) or _build_timeout_message(port),
            }
        except ConnectRefusedError as exc:
            logger.info("_probe_ssh: ip=%s port=%s → refused", ip, port)
            return {
                "ok": False,
                "os_name": "",
                "os_version": "",
                "error_type": "refused",
                "error_message": str(exc) or _build_refused_message(port),
            }
        except Exception as exc:  # noqa: BLE001 — SSH 兜底, 转 failed status
            logger.warning("_probe_ssh: ip=%s → failed (%s: %s)", ip, type(exc).__name__, exc)
            return {
                "ok": False,
                "os_name": "",
                "os_version": "",
                "error_type": "failed",
                "error_message": str(exc),
            }

    @staticmethod
    def _persist_and_dispatch(
        ip: str,
        user: str,
        pwd: str,
        port: int,
        ed,
        provider: str,
        os_name: str,
        os_version: str,
    ) -> dict:
        """写 vps_record(stage=connectable, xray_version="") + vps_task(pending).

        spec v4 路线 B ④:
          - SSHWorker 不查 xray, xray_version 永远写 ""
          - stage 永远 connectable
          - 不写 stage_message (字段已删)

        返回 dict: {vps_id, task_id, stage, os_name, os_version}.
        (无 xray_version 字段, 因为 SSHWorker 路径不持有此信息)
        """
        with session_scope() as s:
            rec = VPSRecord.from_form(
                ip=ip,
                username=user,
                password=pwd,
                port=port,
                os_name=os_name,
                os_version=os_version,
                expire_date=ed,
                provider_domain=provider,
            )
            # spec v4 §5 不变量: stage 永远 connectable, xray_version 永远 ""
            rec.stage = VPSStage.CONNECTABLE
            rec.xray_version = ""
            s.add(rec)
            s.flush()  # 拿 rec.id

            task = VPSTask(vps_id=rec.id, status=TaskStatus.PENDING)
            s.add(task)
            s.flush()  # 拿 task.id

            result = {
                "vps_id": rec.id,
                "task_id": task.id,
                "stage": VPSStage.CONNECTABLE,
                "os_name": os_name,
                "os_version": os_version,
            }
            logger.info(
                "_persist_and_dispatch: ip=%s → vps_id=%s task_id=%s stage=connectable",
                ip, rec.id, task.id,
            )
            return result

    @staticmethod
    def _handle_failure(
        error_type: str,
        error_message: str,
        port: int | None = None,
    ) -> dict:
        """SSH 失败时分场景抛回 (spec v4 §3 路线 C + §5 不变量).

        ⚠️ v4 大改: 全部抛回, **永远不入库**.
        不熔断, 不再重试 (重试已在 _probe_ssh 内部 connect_with_retry 走完).

        返回 dict: {status, message}. **绝不写 DB**.
        """
        if error_type == "auth_failed":
            return {
                "status": "auth_failed",
                "message": _AUTH_FAILED_MESSAGE,
            }
        if error_type == "timeout":
            return {
                "status": "ssh_timeout",
                "message": _build_timeout_message(port if port is not None else 0),
            }
        if error_type == "refused":
            return {
                "status": "ssh_refused",
                "message": _build_refused_message(port if port is not None else 0),
            }
        # 兜底 failed
        return {
            "status": "ssh_failed",
            "message": f"SSH 连接失败: {error_message}",
        }
