"""IPProbeWorker —— rgip MCP 工具的同步段主工人(spec v3)。

干啥:
  接 1 条上游 IP 凭据, 用测试 VPS 校验账密+端口能不能通, 通过 → 入 ip_record
  + 派 ip_task(pending, vps_id=NULL) 给 ProxyDeployWorker。
  跟 SSHWorker 平行(SSHWorker 是 rgvps 的同步段)。

谁会调我:
  tools/rgip.py (MCP 工具入口, T-14)

我用到的工具(完全照 spec v3 §二 工具清单):
  - probe_vps.get_probe_vps_pool / NO_PROBE_VPS_MESSAGE / PROBE_TEST_PORT
  - ssh.session.VPSSession
  - xray.manager.XrayManager.replace_proxy_binding / rollback_proxy_binding
  - xray.config.build_proxy_outbound / generate_random_auth / PROTOCOL_SOCKS5 等
  - xray.service.test_internal_socks(T-12 改后, 含 exit_code/stderr)
  - toolbox.geoip.lookup_egress
  - db.models: IPRecord / IPTask / TaskStatus

私有方法(下划线开头, spec §F 锚点):
  _lookup_by_declared / _lookup_by_actual / _pick_probe_vps /
  _apply_test_outbound / _classify_proxy_error /
  _probe_and_resolve / _persist_and_dispatch / _cleanup_probe

我返回的 status 集 (8 种, spec §2 + ADR-0009):
  - queued (成功)
  - duplicate
  - probe_vps_unreachable (SSH 连不上测试机)
  - probe_vps_not_ready (测试机 SSH 通但 xray 装/起/配 挂; ADR-0009 §决策 §5)
  - proxy_auth_failed / proxy_timeout / proxy_refused / proxy_failed

行为规约金标准: test/ip_probe_worker/spec.md v2 + docs/adr/0009-*.md
"""

from __future__ import annotations

import time
from datetime import date

from db.models import IPRecord, IPTask, TaskStatus
from db.session import session_scope
from log import get_logger
from probe_vps import (
    NO_PROBE_VPS_MESSAGE,
    PROBE_TEST_PORT,
    ProbeVPSSetupFailed,
    ProbeVPSUnreachable,
    bootstrap,
    get_probe_vps_pool,
)
from ssh.session import VPSSession
from toolbox.geoip import lookup_egress
from xray import config as xc
from xray.manager import XrayManager
from xray.service import test_internal_socks


logger = get_logger("services.ip_probe_worker")


# ============ 失败文案常量 (spec v2 §7) ============

_AUTH_FAILED_MESSAGE = (
    "上游代理 {host}:{port} 密码校验失败。"
    "OCR 容易看错 0/O、1/l/I、K/k、c/C 这类字符, 请逐位核对;"
    "另外注意: 服务商面板登录密码 ≠ 代理认证密码, "
    "请回服务商面板复制最新的代理凭据。"
)

_TIMEOUT_MESSAGE = (
    "上游代理 {host}:{port} 连接超时(已重试 3 次)。"
    "可能代理服务暂时挂了 / 入口端口填错 / 测试 VPS 到上游的网络抖动。"
    "建议: 服务商面板核对代理状态后稍后重试。"
)

_REFUSED_MESSAGE = (
    "上游代理 {host}:{port} 被拒绝。"
    "罕见场景: 上游服务可能已停用 / 端口未监听 / 测试 VPS 到该网络的链路被封锁。"
    "建议: 服务商面板核对代理状态。"
)

_FAILED_MESSAGE = "上游代理 {host}:{port} 校验失败: {detail}"

_DUPLICATE_DECLARED_MESSAGE = (
    "这条出口 IP {egress_ip} 已经在库, 无需重复登记。"
)

_DUPLICATE_ACTUAL_MESSAGE = (
    "这条出口 IP(实测) {egress_ip} 已经在库, 无需重复登记。"
)

_QUEUED_MESSAGE = "已入库, 后台 worker 会接手挂到生产 VPS"


# ============ curl exit code 分类 (spec v2 §E) ============

_CURL_REFUSED = 7         # CURLE_COULDNT_CONNECT
_CURL_TIMEOUT = 28        # CURLE_OPERATION_TIMEDOUT
_CURL_PROXY_ERROR = 97    # CURLE_PROXY (常为 socks auth 失败)


# ============ 重试参数 (spec v2 §7) ============

_TIMEOUT_RETRY_ATTEMPTS = 3   # spec §7: timeout 重试 3 次
_TIMEOUT_RETRY_BACKOFF = 2.0  # 秒, 重试间隔


# ============ 内部异常 (工人内部信号, 不暴露给上层) ============

class _ProbeVPSAllDownError(RuntimeError):
    """所有测试 VPS 都连不上, 抛回 probe_vps_unreachable。"""


# ============================================================
# 工人类
# ============================================================

class IPProbeWorker:
    """rgip 入口同步段工人。调用方: tools/rgip.py 的 handler。"""

    def __init__(self) -> None:
        # 工人出生不绑状态, 每次 process 来一条新的
        pass

    # ============ 主入口 ============

    def process(
        self,
        entry_host: str,
        entry_port: int,
        username: str,
        password: str,
        protocol: str,
        declared_egress_ip: str = "",
        provider_domain: str = "",
        expire_date: date | None = None,
        user_label: str = "",
    ) -> dict:
        """同步 8 步主流程, 返回 status + 业务字段 dict (spec §3)。

        参数:
          entry_host / entry_port / username / password / protocol — 上游凭据
          declared_egress_ip — 用户声明的出口 IP, 用于 ① 早期查重(可空)
          provider_domain / expire_date / user_label — 元信息, 入库用

        返回 dict (8 种 status, spec §2 + ADR-0009):
          {"status": "queued", "ip_id", "task_id", "message"}
          {"status": "duplicate", "egress_ip", "message"}
          {"status": "probe_vps_unreachable", "message"}
          {"status": "probe_vps_not_ready", "message"}   # ADR-0009 §5
          {"status": "proxy_auth_failed" / "proxy_timeout" / "proxy_refused" /
                     "proxy_failed", "message"}
        """
        logger.info(
            "开始 rgip: host=%s port=%s user=%s declared=%s",
            entry_host, entry_port, username, declared_egress_ip or "(空)",
        )

        # ① 早期查重 (declared)
        if declared_egress_ip:
            existing = self._lookup_by_declared(declared_egress_ip)
            if existing is not None:
                logger.info(
                    "process: declared=%s → 已存在, 短路 duplicate",
                    declared_egress_ip,
                )
                return {
                    "status": "duplicate",
                    "egress_ip": declared_egress_ip,
                    "message": _DUPLICATE_DECLARED_MESSAGE.format(
                        egress_ip=declared_egress_ip,
                    ),
                }

        # ② 挑测试 VPS
        try:
            probe_entry = self._pick_probe_vps()
        except _ProbeVPSAllDownError as exc:
            logger.warning("process: 测试 VPS 全连不上 (%s)", exc)
            return {
                "status": "probe_vps_unreachable",
                "message": str(exc),
            }

        # ②.5 自举测试机 (ADR-0009): 幂等装好 xray + 起 + 留 19000 inbound.
        # SSH 失败 → probe_vps_unreachable (跟 _pick_probe_vps 同档);
        # 装/起/配 失败 → probe_vps_not_ready (新 status, 区分上游 IP 问题).
        try:
            bootstrap.ensure_ready(probe_entry)
        except ProbeVPSUnreachable as exc:
            logger.warning("process: 测试 VPS ensure_ready SSH 失败 (%s)", exc)
            return {
                "status": "probe_vps_unreachable",
                "message": str(exc),
            }
        except ProbeVPSSetupFailed as exc:
            logger.warning(
                "process: 测试 VPS ensure_ready 装/起/配 失败 (%s)", exc,
            )
            return {
                "status": "probe_vps_not_ready",
                "message": str(exc),
            }

        # ③~⑦ 都跑在 VPSSession + xray + try/finally 兜底里
        last_config: dict | None = None
        session: VPSSession | None = None
        try:
            session = VPSSession(**probe_entry).connect()
            xm = XrayManager(session.client)

            # ③+④ 挂上游凭据当 outbound
            last_config, test_user, test_pwd = self._apply_test_outbound(
                xm, entry_host, entry_port, username, password, protocol,
            )

            # ④+⑤ 内 ping + (通则查 geoip) 或 (不通则分类失败)
            probe_result = self._probe_and_resolve(
                session.client,
                test_user,
                test_pwd,
                entry_host=entry_host,
                entry_port=entry_port,
            )
            if not probe_result["ok"]:
                return probe_result["error_response"]

            actual_egress_ip = probe_result["actual_egress_ip"]
            geo = probe_result["geo"]

            # ⑥ 二次查重 (actual)
            if self._lookup_by_actual(actual_egress_ip) is not None:
                logger.info(
                    "process: actual=%s → 二次查重命中, duplicate",
                    actual_egress_ip,
                )
                return {
                    "status": "duplicate",
                    "egress_ip": actual_egress_ip,
                    "message": _DUPLICATE_ACTUAL_MESSAGE.format(
                        egress_ip=actual_egress_ip,
                    ),
                }

            # ⑦ 同事务入库 + 派任务
            persisted = self._persist_and_dispatch(
                entry_host=entry_host,
                entry_port=entry_port,
                username=username,
                password=password,
                protocol=protocol,
                actual_egress_ip=actual_egress_ip,
                geo=geo,
                provider_domain=provider_domain,
                expire_date=expire_date,
                user_label=user_label,
            )
            logger.info(
                "process: 入库成功 ip_id=%s task_id=%s actual=%s",
                persisted["ip_id"], persisted["task_id"], actual_egress_ip,
            )

            # ⑧ 返回
            return {
                "status": "queued",
                "ip_id": persisted["ip_id"],
                "task_id": persisted["task_id"],
                "egress_ip": actual_egress_ip,
                "message": _QUEUED_MESSAGE,
            }

        except Exception as exc:  # noqa: BLE001 — 兜底未分类异常转 proxy_failed
            logger.warning(
                "process: 未分类异常 (%s: %s) → proxy_failed",
                type(exc).__name__, exc,
            )
            return {
                "status": "proxy_failed",
                "message": _FAILED_MESSAGE.format(
                    host=entry_host, port=entry_port,
                    detail=f"{type(exc).__name__}: {exc}",
                ),
            }
        finally:
            # 兜底拆 19000 残留 (任何路径 + 任何异常都走)
            if session is not None:
                if last_config is not None:
                    try:
                        xm_cleanup = XrayManager(session.client)
                        self._cleanup_probe(xm_cleanup, last_config)
                    except Exception as exc:  # noqa: BLE001 — cleanup 失败不影响业务返回
                        logger.warning(
                            "_cleanup_probe failed: %s: %s",
                            type(exc).__name__, exc,
                        )
                try:
                    session.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("session.close failed: %s", exc)

    # ============ ① + ⑥ 查重 ============

    @staticmethod
    def _lookup_by_declared(declared_egress_ip: str) -> dict | None:
        """DB 查 ip_record.egress_ip = declared, 命中返回快照 dict, 没命中返回 None。"""
        with session_scope() as s:
            rec = (
                s.query(IPRecord)
                .filter_by(egress_ip=declared_egress_ip)
                .first()
            )
            if rec is None:
                return None
            return {"ip_id": rec.id, "egress_ip": rec.egress_ip}

    @staticmethod
    def _lookup_by_actual(actual_egress_ip: str) -> dict | None:
        """二次查重: DB 查 ip_record.egress_ip = actual, 命中返回快照 dict。"""
        with session_scope() as s:
            rec = (
                s.query(IPRecord)
                .filter_by(egress_ip=actual_egress_ip)
                .first()
            )
            if rec is None:
                return None
            return {"ip_id": rec.id, "egress_ip": rec.egress_ip}

    # ============ ② 挑测试 VPS ============

    @staticmethod
    def _pick_probe_vps() -> dict:
        """顺序遍历 PROBE_VPS_POOL, SSH 连得通的返回其字典(给 VPSSession(**dict) 用)。

        全挂抛 _ProbeVPSAllDownError 带 NO_PROBE_VPS_MESSAGE-style 指引。
        空 pool 时 get_probe_vps_pool() 自己抛 RuntimeError, 这里转 _ProbeVPSAllDownError。
        """
        try:
            pool = get_probe_vps_pool()
        except RuntimeError as exc:
            # 空 pool 的指引文案直接透传 (probe_vps 自带)
            raise _ProbeVPSAllDownError(str(exc)) from exc

        last_err: Exception | None = None
        for entry in pool:
            try:
                # 试探性 connect → close, 通了的回退给上层重新建 session 用
                # (避免持有连接太久 + 上层用 with 接管生命周期更干净)
                with VPSSession(**entry):
                    pass
                logger.info(
                    "_pick_probe_vps: 测试 VPS ip=%s 通 → 选定",
                    entry["ip"],
                )
                return entry
            except Exception as exc:  # noqa: BLE001 — 顺序兜底, 任何错都挑下一台
                logger.warning(
                    "_pick_probe_vps: 测试 VPS ip=%s 挂 (%s: %s), 挑下一台",
                    entry["ip"], type(exc).__name__, exc,
                )
                last_err = exc

        raise _ProbeVPSAllDownError(
            f"所有测试 VPS 都连不上, 无法校验。请联系管理员检查测试 VPS 状态。"
            f"最后一次错误: {type(last_err).__name__}: {last_err}"
            if last_err is not None else
            "所有测试 VPS 都连不上, 无法校验。请联系管理员检查测试 VPS 状态。"
        )

    # ============ ③+④ 挂上游 ============

    @staticmethod
    def _apply_test_outbound(
        xm: XrayManager,
        entry_host: str,
        entry_port: int,
        username: str,
        password: str,
        protocol: str,
    ) -> tuple[dict, str, str]:
        """在测试 VPS 上挂上游凭据当 outbound, 返回 (last_config, test_inbound_user, test_inbound_pwd)。

        编排: build_proxy_outbound + generate_random_auth → xm.replace_proxy_binding
        (replace 内部 = remove 旧三件套 + add 新三件套, 一行搞定 spec §3 ③+④)。

        失败抛 xray.config 异常类(透传给 process 转 proxy_failed)。
        """
        proxy_outbound = xc.build_proxy_outbound(
            host=entry_host,
            port=entry_port,
            user=username,
            pwd=password,
            protocol=protocol,
            tag="probe-out",
        )
        test_user, test_pwd = xc.generate_random_auth()
        last_config = xm.replace_proxy_binding(
            PROBE_TEST_PORT,
            proxy_outbound,
            test_user,
            test_pwd,
        )
        logger.info(
            "_apply_test_outbound: port=%s outbound=%s:%s ok",
            PROBE_TEST_PORT, entry_host, entry_port,
        )
        return last_config, test_user, test_pwd

    # ============ ④ curl exit code 分类 ============

    @staticmethod
    def _classify_proxy_error(exit_code: int, stderr: str) -> str:
        """curl exit code → status 字符串(spec v2 §E)。

        分类规则(T-12 stderr 多为空, 主靠 exit_code):
          7  → proxy_refused
          28 → proxy_timeout
          97 → proxy_auth_failed
          其他 → proxy_failed; 但 stderr 含 'auth' / 'SOCKS5' 关键字 → 兜底升 auth_failed

        本方法只在 test_internal_socks 不通时调用(ok=True 不走这里)。
        """
        if exit_code == _CURL_REFUSED:
            return "proxy_refused"
        if exit_code == _CURL_TIMEOUT:
            return "proxy_timeout"
        if exit_code == _CURL_PROXY_ERROR:
            return "proxy_auth_failed"

        # 兜底: 未知 exit_code 但 stderr 关键字含 auth → 软升级
        lower_stderr = (stderr or "").lower()
        if "auth" in lower_stderr or "socks5" in lower_stderr:
            return "proxy_auth_failed"

        return "proxy_failed"

    # ============ ④+⑤ 内 ping + 拿实测出口 ============

    def _probe_and_resolve(
        self,
        client,
        test_user: str,
        test_pwd: str,
        entry_host: str,
        entry_port: int,
    ) -> dict:
        """内 ping 校验 (PROBE_TEST_PORT) + 通则查 geoip。

        timeout 重试 3 次 (spec §7), 其他错误不重试。

        返回:
          {"ok": True, "actual_egress_ip": str, "geo": dict}
          {"ok": False, "error_response": {"status": "...", "message": "..."}}
        """
        last_result: dict | None = None
        for attempt in range(1, _TIMEOUT_RETRY_ATTEMPTS + 1):
            last_result = test_internal_socks(
                client,
                port=PROBE_TEST_PORT,
                user=test_user,
                pwd=test_pwd,
            )
            if last_result.get("ok"):
                actual = (last_result.get("body") or "").strip()
                geo = lookup_egress(actual) if actual else {}
                logger.info(
                    "_probe_and_resolve: 通, actual=%s country=%s",
                    actual, geo.get("country_code", "?"),
                )
                return {"ok": True, "actual_egress_ip": actual, "geo": geo}

            exit_code = int(last_result.get("exit_code") or 0)
            stderr = str(last_result.get("stderr") or "")
            status = self._classify_proxy_error(exit_code, stderr)
            logger.info(
                "_probe_and_resolve: attempt %d/%d 不通 exit=%s status=%s",
                attempt, _TIMEOUT_RETRY_ATTEMPTS, exit_code, status,
            )

            # 只对 timeout 重试 (spec §7)
            if status == "proxy_timeout" and attempt < _TIMEOUT_RETRY_ATTEMPTS:
                time.sleep(_TIMEOUT_RETRY_BACKOFF)
                continue

            # 不重试或重试用完 → 直接返回失败响应
            return {
                "ok": False,
                "error_response": self._build_failure_response(
                    status, entry_host, entry_port, last_result,
                ),
            }

        # 重试循环走完(全 timeout)
        return {
            "ok": False,
            "error_response": self._build_failure_response(
                "proxy_timeout", entry_host, entry_port, last_result or {},
            ),
        }

    @staticmethod
    def _build_failure_response(
        status: str, host: str, port: int, probe_result: dict,
    ) -> dict:
        """status → 错误文案 dict。"""
        if status == "proxy_auth_failed":
            msg = _AUTH_FAILED_MESSAGE.format(host=host, port=port)
        elif status == "proxy_timeout":
            msg = _TIMEOUT_MESSAGE.format(host=host, port=port)
        elif status == "proxy_refused":
            msg = _REFUSED_MESSAGE.format(host=host, port=port)
        else:
            detail = probe_result.get("error") or (
                f"exit_code={probe_result.get('exit_code', '?')}"
            )
            msg = _FAILED_MESSAGE.format(host=host, port=port, detail=detail)
        return {"status": status, "message": msg}

    # ============ ⑦ 入库 + 派任务 ============

    @staticmethod
    def _persist_and_dispatch(
        *,
        entry_host: str,
        entry_port: int,
        username: str,
        password: str,
        protocol: str,
        actual_egress_ip: str,
        geo: dict,
        provider_domain: str,
        expire_date: date | None,
        user_label: str,
    ) -> dict:
        """同事务写 ip_record + ip_task(pending, vps_id=NULL)。

        spec §9 不变量:
          - ip_record.is_active = 1 (ORM default)
          - egress_ip / country_* = 实测值 (geo 来自 lookup_egress)
          - ip_task.vps_id = NULL (谁配的谁写)
        """
        with session_scope() as s:
            ip_rec = IPRecord.from_form(
                entry_host=entry_host,
                entry_port=entry_port,
                username=username,
                password=password,
                protocol=protocol,
                egress_ip=actual_egress_ip,
                provider_domain=provider_domain,
                expire_date=expire_date,
                user_label=user_label,
                geo=geo,
            )
            s.add(ip_rec)
            s.flush()  # 拿 ip_rec.id

            task = IPTask(ip_id=ip_rec.id, status=TaskStatus.PENDING)
            s.add(task)
            s.flush()  # 拿 task.id

            return {"ip_id": ip_rec.id, "task_id": task.id}

    # ============ try/finally 兜底拆三件套 ============

    @staticmethod
    def _cleanup_probe(xm: XrayManager, last_config: dict) -> None:
        """拆 PROBE_TEST_PORT 上的三件套, 测试 VPS 复原。

        失败不抛(上层 finally 已套 try/except), 但记 warning。
        """
        xm.rollback_proxy_binding(PROBE_TEST_PORT, last_config)
        logger.info("_cleanup_probe: port=%s 已拆", PROBE_TEST_PORT)
