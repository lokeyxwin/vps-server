"""ProxyDeployWorker —— 把已登记的上游 IP 挂到生产 VPS 当 socks5 outbound (spec v1).

干啥:
  扫 ip_task 抢一条 → 挑机(同事务抢 vps.stage 资源锁 + 回填 task.vps_id)
  → SSH 进 VPS → 挑端口(排除清单 + 高位随机) → 配上线 + 防火墙放行
  → 内 ping + 外 ping → 同事务一次写: proxy_record / ip / vps / task.

谁会调我:
  - 主进程后台轮询: while True: ProxyDeployWorker().run_once() or sleep
  - 单元测试: 直接 process_task(task_id)

我用到的工具:
  - VPSSession                    (SSH 会话)
  - XrayManager                   (xray 工具箱)
  - xc.build_proxy_outbound       (拼上游 outbound dict)
  - toolbox.firewall              (本机防火墙开端口)
  - toolbox.ports                 (查已用端口 + 推算可用端口)
  - toolbox.proxy_check           (内/外 ping)

行为规约金标准:
  test/proxy_deploy_worker/spec.md v1
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import paramiko
from sqlalchemy import or_

import config as app_config
from db.models import (
    IPRecord,
    IPTask,
    ProxyRecord,
    ProxyStatus,
    TaskStatus,
    VPSRecord,
    VPSStage,
)
from db.session import session_scope
from log import get_logger
from ssh.ops import (
    AuthFailedError,
    ConnectRefusedError,
    ConnectTimeoutError,
)
from ssh.session import VPSSession
from toolbox import firewall
from toolbox.ports import (
    COMMON_RESERVED_PORTS,
    compute_available_ports,
    get_used_ports,
)
from toolbox.proxy_check import test_external, test_internal
from xray import config as xc
from xray.manager import XrayManager
from xray.service import ReloadFailedError


logger = get_logger("services.proxy_deploy_worker")


# ============================================================
# 常量
# ============================================================

PORT_RANGE_LOW = 1024
PORT_RANGE_HIGH = 65535
LOCK_TIMEOUT_MINUTES = 5
MAX_RETRY_COUNT = 5
RETRY_BACKOFF_CAP_MINUTES = 60


# apply 阶段可能抛的全套 xray 异常(无论 add / upload / validate / reload)
_APPLY_BINDING_ERRORS = (
    xc.PortConflictError,
    xc.PortAlreadyBoundError,
    xc.OutboundTagConflictError,
    xc.ConfigWriteError,
    xc.ConfigValidationError,
    xc.ConfigReadError,
    ReloadFailedError,
)


# ============================================================
# 工人类
# ============================================================

class ProxyDeployWorker:
    """把已登记的上游 IP 挂到生产 VPS 当 socks5 outbound 的工人."""

    def __init__(self, worker_id: str | None = None) -> None:
        self._worker_id = worker_id or f"proxy_deploy_worker_pid{os.getpid()}"

    # ============ 主入口 ============

    def run_once(self) -> int:
        """轮询一次. 抢到任务并处理完返回 1; 无可领返回 0."""
        task_id = self._claim_task()
        if task_id is None:
            return 0
        self.process_task(task_id)
        return 1

    def process_task(self, task_id: int) -> dict:
        """处理指定 task. 测试可绕过 _claim_task 直接调.

        返回 dict:
          {"status": "done", "task_id", "vps_id", "vps_port", "outer_ping_ok"}
          {"status": "failed", "last_error_code"}
          {"status": "retriable", "last_error_code"}
          {"status": "skipped", "reason"}
        """
        creds = self._load_credentials(task_id)
        if creds is None:
            return {"status": "skipped", "reason": "task_or_ip_missing"}

        logger.info(
            "开始处理 ip_task_id=%s ip_id=%s entry=%s:%s",
            task_id, creds["ip_id"], creds["entry_host"], creds["entry_port"],
        )

        # ① 挑 VPS + 同事务抢资源锁 + 回填 task.vps_id (spec §4)
        vps_pick = self._pick_vps_and_lock(task_id)
        if vps_pick is None:
            logger.info("ip_task_id=%s 没机可挑 → no_vps_capacity (终态)", task_id)
            self._mark_failed(
                task_id,
                "no_vps_capacity",
                "VPS 池子满了或没有装好 xray 的机, 用户需加机或释放过期机",
            )
            return {"status": "failed", "last_error_code": "no_vps_capacity"}

        vps_id = vps_pick["vps_id"]
        vps_ip = vps_pick["ip"]

        # ② SSH 进 VPS → 挑端口 → 配上线 → 验证 → 收尾
        try:
            with VPSSession(vps_ip, vps_pick["username"], vps_pick["password"], vps_pick["port"]) as sess:
                client = sess.client

                vps_port = self._pick_port(client, vps_id)
                if vps_port is None:
                    logger.info(
                        "ip_task_id=%s vps_id=%s 端口候选池空 → no_port_available",
                        task_id, vps_id,
                    )
                    self._mark_failed(
                        task_id,
                        "no_port_available",
                        "VPS 端口候选池空(1024-65535 全被占或全被排除)",
                    )
                    return {"status": "failed", "last_error_code": "no_port_available"}

                deploy = self._deploy_one_binding(
                    client=client,
                    vps_ip=vps_ip,
                    vps_port=vps_port,
                    creds=creds,
                )
                if deploy["status"] == "failed":
                    self._mark_failed(
                        task_id,
                        deploy["last_error_code"],
                        deploy["last_error_msg"],
                    )
                    return {
                        "status": "failed",
                        "last_error_code": deploy["last_error_code"],
                    }

                # ③ 成功收尾 (spec §6)
                self._mark_done(
                    task_id=task_id,
                    vps_id=vps_id,
                    vps_port=vps_port,
                    ip_id=creds["ip_id"],
                    inbound_user=deploy["inbound_user"],
                    inbound_pwd=deploy["inbound_pwd"],
                    upstream_host=creds["entry_host"],
                    egress_ip=creds["egress_ip"],
                    egress_country=creds["country_code"],
                    outer_ping_ok=deploy["outer_ping_ok"],
                )
                logger.info(
                    "ip_task_id=%s 完工: vps=%s:%s outer_ping_ok=%s",
                    task_id, vps_id, vps_port, deploy["outer_ping_ok"],
                )
                return {
                    "status": "done",
                    "task_id": task_id,
                    "vps_id": vps_id,
                    "vps_port": vps_port,
                    "outer_ping_ok": deploy["outer_ping_ok"],
                }

        except AuthFailedError as exc:
            # 抢机后 SSH 突然 auth 失败 → 上游可能改了密码, 不重试
            logger.warning("ip_task_id=%s SSH auth failed: %s", task_id, exc)
            self._mark_failed(task_id, "ssh_disconnected", f"SSH auth failed: {exc}")
            return {"status": "failed", "last_error_code": "ssh_disconnected"}
        except (ConnectTimeoutError, ConnectRefusedError, paramiko.SSHException) as exc:
            logger.warning(
                "ip_task_id=%s SSH 中途断 (%s: %s) → retriable",
                task_id, type(exc).__name__, exc,
            )
            self._handle_retriable(task_id, "ssh_disconnected", str(exc))
            return {"status": "retriable", "last_error_code": "ssh_disconnected"}
        except Exception as exc:  # noqa: BLE001 — 兜底未分类异常转 retriable
            logger.warning(
                "ip_task_id=%s 未分类异常 (%s: %s) → retriable",
                task_id, type(exc).__name__, exc,
            )
            self._handle_retriable(
                task_id,
                "ssh_disconnected",
                f"{type(exc).__name__}: {exc}",
            )
            return {"status": "retriable", "last_error_code": "ssh_disconnected"}

    # ============ 抢任务 ============

    def _claim_task(self) -> int | None:
        """从 ip_task 抢一条 PENDING. 原子 UPDATE 防多 worker 并发抢到同一条."""
        now = datetime.utcnow()
        with session_scope() as s:
            candidate = (
                s.query(IPTask)
                .filter(
                    IPTask.status == TaskStatus.PENDING,
                    IPTask.next_run_at <= now,
                    or_(
                        IPTask.locked_until.is_(None),
                        IPTask.locked_until < now,
                    ),
                )
                .order_by(IPTask.next_run_at)
                .first()
            )
            if candidate is None:
                return None

            rows = (
                s.query(IPTask)
                .filter(
                    IPTask.id == candidate.id,
                    IPTask.status == TaskStatus.PENDING,
                )
                .update(
                    {
                        IPTask.status: TaskStatus.IN_PROGRESS,
                        IPTask.worker_id: self._worker_id,
                        IPTask.locked_until: now + timedelta(minutes=LOCK_TIMEOUT_MINUTES),
                    },
                    synchronize_session=False,
                )
            )
            if rows == 0:
                return None
            return candidate.id

    @staticmethod
    def _load_credentials(task_id: int) -> dict | None:
        """读 task → ip_record, 抽出上游凭据快照(挑机前不读 VPS, 因为 task.vps_id 还是 NULL).

        返回:
          {"ip_id", "entry_host", "entry_port", "username", "password",
           "protocol", "egress_ip", "country_code"}
          None: task 或 ip 缺失
        """
        with session_scope() as s:
            task = s.get(IPTask, task_id)
            if task is None:
                logger.warning("_load_credentials: task_id=%s 不存在", task_id)
                return None
            ip = s.get(IPRecord, task.ip_id)
            if ip is None:
                logger.warning(
                    "_load_credentials: task_id=%s ip_id=%s 不存在",
                    task_id, task.ip_id,
                )
                return None
            return {
                "ip_id": ip.id,
                "entry_host": ip.entry_host,
                "entry_port": ip.entry_port,
                "username": ip.username,
                "password": ip.get_password(),
                "protocol": ip.protocol,
                "egress_ip": ip.egress_ip,
                "country_code": ip.country_code,
            }

    # ============ 挑 VPS + 抢资源锁 (同事务两写, spec §4) ============

    @staticmethod
    def _pick_vps_and_lock(task_id: int) -> dict | None:
        """挑一台符合 4 条件的 VPS, 同事务抢 vps.stage 资源锁 + 回填 task.vps_id.

        SQL: stage='connectable' AND xray_version!='' AND is_active=1
             AND used_port_count<MAX_PORTS_PER_VPS
        ORDER BY used_port_count ASC, RANDOM() LIMIT 1

        返回:
          {"vps_id", "ip", "username", "password", "port"}: 抢机成功
          None: 没机可挑(spec §7 → no_vps_capacity 终态)
        """
        from sqlalchemy.sql import func as sql_func

        with session_scope() as s:
            vps = (
                s.query(VPSRecord)
                .filter(
                    VPSRecord.stage == VPSStage.CONNECTABLE,
                    VPSRecord.xray_version != "",
                    VPSRecord.is_active == 1,
                    VPSRecord.used_port_count < app_config.MAX_PORTS_PER_VPS,
                )
                .order_by(VPSRecord.used_port_count.asc(), sql_func.random())
                .first()
            )
            if vps is None:
                return None

            # 同事务两写: vps.stage='running' + task.vps_id=vps.id (spec §4 不变量)
            vps.stage = VPSStage.RUNNING
            task = s.get(IPTask, task_id)
            if task is not None:
                task.vps_id = vps.id

            return {
                "vps_id": vps.id,
                "ip": vps.ip,
                "username": vps.username,
                "password": vps.get_password(),
                "port": vps.port,
            }

    # ============ 挑端口 (spec §5) ============

    @staticmethod
    def _pick_port(client: paramiko.SSHClient, vps_id: int) -> int | None:
        """走 spec §5 端口算法, 返候选端口, 候选池空时返 None.

        候选池 = range(1024, 65536)
              - VPS 真实在监听的所有端口
              - COMMON_RESERVED_PORTS (即 ADR-0006 §6 EXCLUDED_PORTS)
              - {XRAY_DEFAULT_PORT}
              - {该 VPS 已用 proxy_record.vps_port (status='using')}
        然后高位随机挑一个.
        """
        used_on_vps = get_used_ports(client, PORT_RANGE_LOW, PORT_RANGE_HIGH)

        with session_scope() as s:
            already_bound = {
                row[0] for row in (
                    s.query(ProxyRecord.vps_port)
                    .filter(
                        ProxyRecord.vps_id == vps_id,
                        ProxyRecord.status == ProxyStatus.USING,
                    )
                    .all()
                )
            }

        exclude = (
            set(COMMON_RESERVED_PORTS)
            | {app_config.XRAY_DEFAULT_PORT}
            | already_bound
        )

        available = compute_available_ports(
            used=used_on_vps,
            start_port=PORT_RANGE_LOW,
            end_port=PORT_RANGE_HIGH,
            exclude=exclude,
        )
        if not available:
            return None

        # 高位随机: secrets 安全随机一个 (避免 random 模块的可预测性)
        import secrets
        return secrets.choice(sorted(available))

    # ============ 配上线 + 防火墙 + 内/外 ping (spec §3 步骤 4-5) ============

    def _deploy_one_binding(
        self,
        client: paramiko.SSHClient,
        vps_ip: str,
        vps_port: int,
        creds: dict,
    ) -> dict:
        """配上线 + 防火墙 + 内 ping + 外 ping.

        返回:
          {"status": "success", "outer_ping_ok": bool,
           "inbound_user": str, "inbound_pwd": str}
          {"status": "failed", "last_error_code": str, "last_error_msg": str}

        失败时本方法内部已 rollback 三件套, 调用方不再处理 xray 状态.
        """
        # inbound 账密生成 (需求窗口拍板 2026-06-09, b 方案):
        #   user = "proxy_<ip_id>"  ; pwd = uuid4().hex (32 字符)
        inbound_user = f"proxy_{creds['ip_id']}"
        inbound_pwd = uuid4().hex

        proxy_outbound = xc.build_proxy_outbound(
            host=creds["entry_host"],
            port=creds["entry_port"],
            user=creds["username"],
            pwd=creds["password"],
            protocol=creds["protocol"],
            tag=f"proxy-out-{vps_port}",
        )

        xm = XrayManager(client)
        last_config: dict | None = None

        # 步骤 4a: xray apply 三件套
        try:
            last_config = xm.apply_proxy_binding(
                vps_port, proxy_outbound, inbound_user, inbound_pwd,
            )
        except _APPLY_BINDING_ERRORS as exc:
            logger.warning(
                "_deploy_one_binding: apply_proxy_binding 失败 (%s: %s)",
                type(exc).__name__, exc,
            )
            # apply 抛错时 last_config 仍是 None, 服务器 config 可能部分写入也可能没写,
            # 但已没法定位回滚目标. 留给运维人工核查 (spec §7 注: 已 rollback 含义是"尽力而为")
            return {
                "status": "failed",
                "last_error_code": "apply_binding_failed",
                "last_error_msg": f"{type(exc).__name__}: {exc}",
            }

        # 步骤 4b: 本机防火墙放行 vps_port
        try:
            firewall.open_tcp_port_range(client, vps_port, vps_port)
        except firewall.FirewallOpenError as exc:
            logger.warning(
                "_deploy_one_binding: firewall 放行 %d 失败 (%s), rollback xray",
                vps_port, exc,
            )
            self._safe_rollback(xm, vps_port, last_config)
            return {
                "status": "failed",
                "last_error_code": "firewall_open_failed",
                "last_error_msg": str(exc),
            }

        # 步骤 5a: 内 ping (在 VPS 本机走 127.0.0.1:vps_port)
        inner_ok, _egress_from_inner = test_internal(
            client=client,
            port=vps_port,
            user=inbound_user,
            pwd=inbound_pwd,
        )
        if not inner_ok:
            logger.warning(
                "_deploy_one_binding: 内 ping vps_port=%d 不通, rollback 三件套",
                vps_port,
            )
            self._safe_rollback(xm, vps_port, last_config)
            return {
                "status": "failed",
                "last_error_code": "inner_ping_failed",
                "last_error_msg": f"内 ping vps_port={vps_port} 不通, 已 rollback",
            }

        # 步骤 5b: 外 ping (本机 worker → VPS_IP:vps_port)
        # 不通不算失败 (spec §8: 外部安全策略组不归本工人管), 走 pending_fw 状态
        outer_ok = test_external(
            host=vps_ip,
            port=vps_port,
            user=inbound_user,
            pwd=inbound_pwd,
        )

        return {
            "status": "success",
            "outer_ping_ok": outer_ok,
            "inbound_user": inbound_user,
            "inbound_pwd": inbound_pwd,
        }

    @staticmethod
    def _safe_rollback(
        xm: XrayManager,
        vps_port: int,
        last_config: dict | None,
    ) -> None:
        """rollback 三件套, 失败只记日志(已经在失败路径上, 不再升级)."""
        if last_config is None:
            return
        try:
            xm.rollback_proxy_binding(vps_port, last_config)
        except Exception as exc:  # noqa: BLE001 — rollback 失败不影响主流程返回
            logger.warning(
                "_safe_rollback: vps_port=%d rollback 失败 (%s: %s)",
                vps_port, type(exc).__name__, exc,
            )

    # ============ 收尾 (spec §6) ============

    @staticmethod
    def _mark_done(
        *,
        task_id: int,
        vps_id: int,
        vps_port: int,
        ip_id: int,
        inbound_user: str,
        inbound_pwd: str,
        upstream_host: str,
        egress_ip: str,
        egress_country: str,
        outer_ping_ok: bool,
    ) -> None:
        """成功收尾: 同事务一次写 proxy_record / vps / task (spec §6).

        - proxy_record: INSERT 新行, status = using 或 pending_fw
        - vps: used_port_count +1, stage='running' → 'connectable' (释放资源锁)
        - ip_task: in_progress → done
        """
        now = datetime.utcnow()
        with session_scope() as s:
            proxy = ProxyRecord.from_new_deployment(
                vps_id=vps_id,
                vps_port=vps_port,
                ip_id=ip_id,
                inbound_user=inbound_user,
                inbound_pwd=inbound_pwd,
                upstream_host=upstream_host,
                egress_ip=egress_ip,
                egress_country=egress_country,
                protocol="socks5",
            )
            proxy.status = ProxyStatus.USING if outer_ping_ok else ProxyStatus.PENDING_FW
            s.add(proxy)

            vps = s.get(VPSRecord, vps_id)
            if vps is not None:
                vps.used_port_count = (vps.used_port_count or 0) + 1
                # 释放资源锁回池子, 让其他工人/业务能拿这台机 (ADR-0005 §1)
                vps.stage = VPSStage.CONNECTABLE

            task = s.get(IPTask, task_id)
            if task is not None:
                task.status = TaskStatus.DONE
                task.completed_at = now
                task.locked_until = None
                task.worker_id = ""
                task.last_error_code = ""
                task.last_error_msg = ""

    # ============ 失败分流 (spec §7) ============

    @staticmethod
    def _mark_failed(
        task_id: int,
        error_code: str,
        error_msg: str,
    ) -> None:
        """终态失败: status=FAILED.

        spec §7 + ADR-0005 §3: vps.stage 保持 running 不释放(等维修工人/人工介入).
        no_vps_capacity 例外: 没抢机, 不会动 vps.stage.
        """
        with session_scope() as s:
            task = s.get(IPTask, task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.last_error_code = error_code
            task.last_error_msg = error_msg[:255]
            task.locked_until = None
            task.worker_id = ""

    @staticmethod
    def _handle_retriable(
        task_id: int,
        error_code: str,
        error_msg: str,
    ) -> None:
        """可重试失败: retry_count < N → 回 PENDING + 退避; >= N → FAILED.

        spec §7: 只有 ssh_disconnected 走此路径, 结构性容量问题(no_*)直接 mark_failed.
        失败时 vps.stage 保持 running 不释放(交给维修工人, ADR-0005 §3).
        """
        now = datetime.utcnow()
        with session_scope() as s:
            task = s.get(IPTask, task_id)
            if task is None:
                return
            next_retry = task.retry_count + 1
            task.retry_count = next_retry
            task.last_error_code = error_code
            task.last_error_msg = error_msg[:255]
            task.locked_until = None
            task.worker_id = ""

            if next_retry >= MAX_RETRY_COUNT:
                task.status = TaskStatus.FAILED
            else:
                backoff_minutes = min(2 ** next_retry, RETRY_BACKOFF_CAP_MINUTES)
                task.status = TaskStatus.PENDING
                task.next_run_at = now + timedelta(minutes=backoff_minutes)
