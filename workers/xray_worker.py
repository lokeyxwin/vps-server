"""XrayWorker —— 装机 / 启停 / 自启 / 纳管工 (rgvps 异步段, spec v5.1).

干啥:
  扫 vps_task 抢一条 → SSH 进 VPS → 按 xray 现状走 3 个前置分支 (A/B/C)
  → 不管哪个分支都跑统一收尾 (读配置 + 直进直出兜底 + 纳管 / remove + reload)
  → 成功标 task=done + vps.stage=running.

谁会调我:
  - 主进程后台轮询: while True: XrayWorker().run_once() or sleep
  - 单元测试: 直接 process_task(task_id)

我用到的工具:
  - VPSSession            (SSH 会话, 有状态用类, 进出 with 自动断连)
  - XrayManager           (xray 工具箱, 有状态用类)
  - toolbox.proxy_check   (内 / 外 ping)
  - toolbox.geoip         (出口 IP 查国家)
  - toolbox.security      (密码加密)
  - xray.config           (remove_proxy_binding 智能删三件套)

行为规约金标准:
  test/xray_worker/spec.md v5.1
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta

from db.models import (
    IPRecord,
    ProxyRecord,
    ProxyStatus,
    TaskStatus,
    VPSRecord,
    VPSStage,
    VPSTask,
)
from db.session import session_scope
from log import get_logger
from ssh.ops import (
    AuthFailedError,
    ConnectRefusedError,
    ConnectTimeoutError,
)
from ssh.session import VPSSession
from toolbox.geoip import lookup_egress
from toolbox.proxy_check import test_internal
from toolbox.security import encrypt_password
from xray import config as xc
from xray.manager import XrayManager
from xray.service import (
    EnableFailedError,
    InstallFailedError,
    ReloadFailedError,
    ServiceNotActiveError,
    VerifyFailedError,
    XrayError,
)
from sqlalchemy import or_


logger = get_logger("services.xray_worker")


# ============================================================
# 常量
# ============================================================

DEFAULT_PORT_CEILING = 18440
DEFAULT_PORT_FLOOR = 1024
LOCK_TIMEOUT_MINUTES = 5
MAX_RETRY_COUNT = 5
RETRY_BACKOFF_CAP_MINUTES = 60


# ============================================================
# 错误类
# ============================================================

class NoDefaultPortError(RuntimeError):
    """默认入口端口让步算法从 18440 降到 1024 仍找不到空位."""


# ============================================================
# 内部纯函数: 让步算法 + 添加默认入口
# ============================================================

def _find_default_port(cfg: dict) -> int:
    """从 18440 降 1 找空位, 下限 1024. 找不到抛 NoDefaultPortError."""
    occupied: set[int] = set()
    for inb in (cfg.get("inbounds", []) or []):
        port = inb.get("port")
        if isinstance(port, int):
            occupied.add(port)
    for port in range(DEFAULT_PORT_CEILING, DEFAULT_PORT_FLOOR - 1, -1):
        if port not in occupied:
            return port
    raise NoDefaultPortError(
        "18440 → 1024 全部占满, 无法分配默认入口端口"
    )


def _append_default_direct(cfg: dict, port: int) -> dict:
    """往 config 追加一条 socks5 noauth → freedom 直进直出三件套. deepcopy 入参."""
    new = copy.deepcopy(cfg) if cfg else {}
    new.setdefault("log", {"loglevel": "warning"})
    new.setdefault("inbounds", [])
    new.setdefault("outbounds", [])
    new.setdefault("routing", {})
    new["routing"].setdefault("rules", [])

    existing_outbound_tags = {ob.get("tag", "") for ob in new["outbounds"]}
    existing_inbound_tags = {inb.get("tag", "") for inb in new["inbounds"]}

    outbound_tag = "direct"
    if outbound_tag not in existing_outbound_tags:
        new["outbounds"].append({"tag": outbound_tag, "protocol": "freedom"})

    inbound_tag = "default-direct"
    if inbound_tag in existing_inbound_tags:
        inbound_tag = f"default-direct-{port}"

    new["inbounds"].append({
        "tag": inbound_tag,
        "port": port,
        "listen": "0.0.0.0",
        "protocol": "socks",
        "settings": {"auth": "noauth", "udp": True},
    })

    new["routing"]["rules"].append({
        "type": "field",
        "inboundTag": [inbound_tag],
        "outboundTag": outbound_tag,
    })

    return new


def _outbound_protocol_to_user(outbound_protocol: str) -> str:
    """xray 协议名 → ip_record 用的业务层名."""
    if outbound_protocol in ("socks", "socks5"):
        return "socks5"
    if outbound_protocol == "http":
        return "http"
    return outbound_protocol or "socks5"


# ============================================================
# 工人类
# ============================================================

class XrayWorker:
    """xray 装机 / 启停 / 自启 / 纳管工."""

    def __init__(self) -> None:
        # 工人出生时不绑 task / SSH, 每次 run_once 抢新的
        self._worker_id = f"xray_worker_pid{os.getpid()}"

    # ============ 主入口 ============

    def run_once(self) -> int:
        """轮询一次. 抢到任务并处理完返回 1; 无可领返回 0."""
        task_id = self._claim_task()
        if task_id is None:
            return 0
        self.process_task(task_id)
        return 1

    def process_task(self, task_id: int) -> None:
        """处理指定 task. 测试可绕过 _claim_task 直接调."""
        creds = self._load_credentials(task_id)
        if creds is None:
            return

        # 抢到 task 后立刻占 VPS 资源锁 (ADR-0005 §决策 §1)
        # SSH 之前先标 running, 防止别的工人/部门同时操作这台机
        self._lock_vps_resource(creds["vps_id"])

        ip = creds["ip"]
        logger.info(
            "开始处理 task_id=%s vps_id=%s ip=%s",
            task_id, creds["vps_id"], ip,
        )

        try:
            with VPSSession(ip, creds["user"], creds["pwd"], creds["port"]) as sess:
                xray = XrayManager(sess.client)

                branch = self._classify(xray)
                logger.info("现状判断: task_id=%s → 分支 %s", task_id, branch)

                if branch == "A":
                    self._prepare_fresh(xray)
                elif branch == "B":
                    self._prepare_stopped(xray)
                else:
                    self._prepare_running(xray)

                tail_result = self._unified_tail(sess.client, xray, creds["vps_id"])

            self._mark_done(task_id, creds["vps_id"], tail_result)
            logger.info(
                "task_id=%s 完工: version=%s default_port=%s used_count=%s",
                task_id, tail_result["xray_version"],
                tail_result["default_inbound_port"],
                tail_result["used_port_count"],
            )

        except AuthFailedError as exc:
            self._mark_failed(task_id, "auth_denied", str(exc))
        except ConnectTimeoutError as exc:
            self._handle_retriable(task_id, "ssh_timeout", str(exc))
        except ConnectRefusedError as exc:
            self._handle_retriable(task_id, "ssh_refused", str(exc))
        except NoDefaultPortError as exc:
            self._mark_failed(task_id, "no_default_port", str(exc))
        except XrayError as exc:
            self._handle_retriable(task_id, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 — 兜底未分类异常转 retriable
            logger.warning(
                "process_task: task_id=%s 未分类异常 (%s: %s)",
                task_id, type(exc).__name__, exc,
            )
            self._handle_retriable(task_id, "install_failed", f"{type(exc).__name__}: {exc}")

    # ============ 抢任务 (原子锁) ============

    def _claim_task(self) -> int | None:
        """从 vps_task 抢一条 PENDING. 原子 UPDATE 防多 worker 并发抢到同一条."""
        now = datetime.now()
        with session_scope() as s:
            candidate = (
                s.query(VPSTask)
                .filter(
                    VPSTask.status == TaskStatus.PENDING,
                    VPSTask.next_run_at <= now,
                    or_(
                        VPSTask.locked_until.is_(None),
                        VPSTask.locked_until < now,
                    ),
                )
                .order_by(VPSTask.next_run_at)
                .first()
            )
            if candidate is None:
                return None

            rows = (
                s.query(VPSTask)
                .filter(
                    VPSTask.id == candidate.id,
                    VPSTask.status == TaskStatus.PENDING,
                )
                .update(
                    {
                        VPSTask.status: TaskStatus.IN_PROGRESS,
                        VPSTask.worker_id: self._worker_id,
                        VPSTask.locked_until: now + timedelta(minutes=LOCK_TIMEOUT_MINUTES),
                    },
                    synchronize_session=False,
                )
            )
            if rows == 0:
                return None
            return candidate.id

    @staticmethod
    def _lock_vps_resource(vps_id: int) -> None:
        """抢到 task 后, 把 VPS 资源锁标为 running (ADR-0005 §决策 §1).

        VPS 资源锁 vs 任务并发锁:
          - 资源锁 (vps_record.stage='running') 跨工人/部门, 告诉别的业务"这台机有人在用"
          - 任务锁 (vps_task.status='in_progress' + locked_until) 同任务并发, 防多工人抢同一张任务单
        两层职责不同, 抢到 task 后必须显式占 stage 锁.
        """
        with session_scope() as s:
            vps = s.get(VPSRecord, vps_id)
            if vps is not None:
                vps.stage = VPSStage.RUNNING

    @staticmethod
    def _load_credentials(task_id: int) -> dict | None:
        """读 task + 对应 vps_record, 抽出 SSH 凭据."""
        with session_scope() as s:
            task = s.get(VPSTask, task_id)
            if task is None:
                logger.warning("_load_credentials: task_id=%s 不存在", task_id)
                return None
            vps = s.get(VPSRecord, task.vps_id)
            if vps is None:
                logger.warning(
                    "_load_credentials: task_id=%s vps_id=%s 不存在",
                    task_id, task.vps_id,
                )
                return None
            return {
                "vps_id": vps.id,
                "ip": vps.ip,
                "user": vps.username,
                "pwd": vps.get_password(),
                "port": vps.port,
            }

    # ============ 现状判断 ============

    @staticmethod
    def _classify(xray: XrayManager) -> str:
        """实时探测 xray 现状, 返回分支代号 'A' / 'B' / 'C'.

        拿到任务后实时探 `xray version`: 有版本 = 已装(走 B/C), 无版本 = 没装(走 A)。
        不看 DB 的 xray_version —— 那是 XrayWorker 完工时自己写的产出, 第一次进来必为空,
        拿它当判据会把"已装在跑"的机器误判成全新去重装。
        """
        if not xray.version():
            return "A"
        if not xray.is_running():
            return "B"
        return "C"

    # ============ 前置分支 ============

    @staticmethod
    def _prepare_fresh(xray: XrayManager) -> None:
        """分支 A: 装 → 写默认 config → 起 → 自启 → 验证版本 + is_running."""
        xray.install()
        if xray.is_config_blank():
            xray.write_default_config()
        xray.start()
        xray.enable()
        v = xray.version()
        if not v:
            raise VerifyFailedError("xray 装完拿不到版本号")
        if not xray.is_running():
            raise ServiceNotActiveError("xray 装完启动后服务仍非 active")

    @staticmethod
    def _prepare_stopped(xray: XrayManager) -> None:
        """分支 B: 看自启 → 起 → 验证."""
        if not xray.is_enabled():
            xray.enable()
        xray.start()
        if not xray.is_running():
            raise ServiceNotActiveError("xray start 后服务仍非 active")

    @staticmethod
    def _prepare_running(xray: XrayManager) -> None:
        """分支 C: 看自启没设就设, 其他啥不做."""
        if not xray.is_enabled():
            xray.enable()

    # ============ 统一收尾 ============

    def _unified_tail(self, client, xray: XrayManager, vps_id: int) -> dict:
        """读配置 → 分类 → 确保直进直出 → 纳管/remove → upload → validate → reload → 验证."""
        outbounds = xray.extract_existing_outbounds()
        direct_entries = [o for o in outbounds if o["outbound_protocol"] == "freedom"]
        proxy_entries = [o for o in outbounds if o["outbound_protocol"] != "freedom"]

        if outbounds:
            cfg = xc.read_config(client, use_sudo=xray.use_sudo)
            if not cfg:
                cfg = xc.build_vps_direct_config()
        else:
            cfg = (
                xc.build_vps_direct_config()
                if xc.is_config_blank(client, use_sudo=xray.use_sudo)
                else xc.read_config(client, use_sudo=xray.use_sudo)
            )

        if direct_entries:
            default_port = direct_entries[0]["vps_port"]
        else:
            default_port = _find_default_port(cfg)
            cfg = _append_default_direct(cfg, default_port)

        used_count = 0
        for entry in proxy_entries:
            ok, egress_ip = test_internal(
                client=client,
                port=entry["vps_port"],
                user=entry["inbound_user"],
                pwd=entry["inbound_pwd"],
            )
            if ok:
                self._upsert_managed(vps_id, entry, egress_ip)
                used_count += 1
            else:
                cfg = xc.remove_proxy_binding(cfg, entry["vps_port"])

        xray.upload_config(cfg)
        xray.validate_config()
        xray.reload()

        if not xray.is_running():
            raise ServiceNotActiveError("reload 后 xray 未在跑")

        return {
            "xray_version": xray.version(),
            "default_inbound_port": default_port,
            "used_port_count": used_count,
        }

    # ============ 纳管入库 ============

    @staticmethod
    def _upsert_managed(vps_id: int, entry: dict, egress_ip: str) -> None:
        """通的代理出口写库: upsert ip_record (按 egress_ip) + write proxy_record."""
        geo = lookup_egress(egress_ip) if egress_ip else {}
        country_code = geo.get("country_code", "")

        with session_scope() as s:
            ip_rec = (
                s.query(IPRecord)
                .filter_by(egress_ip=egress_ip)
                .first()
                if egress_ip else None
            )
            if ip_rec is None:
                ip_rec = IPRecord(
                    entry_host=entry["upstream_host"],
                    entry_port=entry["upstream_port"] or 0,
                    username=entry["upstream_user"],
                    password_encrypted=encrypt_password(entry["upstream_pwd"]),
                    protocol=_outbound_protocol_to_user(entry["outbound_protocol"]),
                    egress_ip=egress_ip,
                    country_code=country_code,
                    country_name=geo.get("country_name", ""),
                    city=geo.get("city", ""),
                    region_name=geo.get("region_name", ""),
                    provider_domain="",
                    expire_date=None,
                    is_active=1,
                )
                s.add(ip_rec)
                s.flush()

            s.add(ProxyRecord(
                vps_id=vps_id,
                vps_port=entry["vps_port"],
                ip_id=ip_rec.id,
                protocol="socks5",
                inbound_user=entry["inbound_user"],
                inbound_pwd_encrypted=encrypt_password(entry["inbound_pwd"]),
                upstream_host=entry["upstream_host"],
                egress_ip=egress_ip,
                egress_country=country_code,
                status=ProxyStatus.USING,
            ))

    # ============ 完工 / 失败分流 ============

    @staticmethod
    def _mark_done(task_id: int, vps_id: int, tail_result: dict) -> None:
        now = datetime.now()
        with session_scope() as s:
            task = s.get(VPSTask, task_id)
            if task is not None:
                task.status = TaskStatus.DONE
                task.completed_at = now
                task.locked_until = None
                task.worker_id = ""
                task.last_error_code = ""
                task.last_error_msg = ""
            vps = s.get(VPSRecord, vps_id)
            if vps is not None:
                # 完工释放资源锁回池子, 让 ProxyDeployWorker 等后续工人能拿这台机 (ADR-0005 §决策 §1)
                vps.stage = VPSStage.CONNECTABLE
                vps.xray_version = tail_result["xray_version"]
                vps.used_port_count = tail_result["used_port_count"]
                vps.xray_installed_at = vps.xray_installed_at or now
                vps.xray_last_checked_at = now

    @staticmethod
    def _mark_failed(task_id: int, error_code: str, error_msg: str) -> None:
        """不可重试失败: status=FAILED."""
        with session_scope() as s:
            task = s.get(VPSTask, task_id)
            if task is None:
                return
            task.status = TaskStatus.FAILED
            task.last_error_code = error_code
            task.last_error_msg = error_msg[:255]
            task.locked_until = None
            task.worker_id = ""

    @staticmethod
    def _handle_retriable(task_id: int, error_code: str, error_msg: str) -> None:
        """可重试失败: retry_count < 5 → 回 PENDING + 退避; >= 5 → FAILED."""
        now = datetime.now()
        with session_scope() as s:
            task = s.get(VPSTask, task_id)
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
