"""probe_vps.bootstrap — 测试 VPS 自举模块 (ADR-0009 §3).

干啥:
  幂等装好测试 VPS 自身的 xray 基础设施, 让 IPProbeWorker 后续能挂上游凭据
  做内 ping 校验. 6 步幂等:
    ① 拿入参 entry (调用方处理 pool / slot 边界)
    ② SSH 连 → 失败抛 ProbeVPSUnreachable
    ③ xray 没装 → install (失败抛 ProbeVPSSetupFailed)
    ④ xray 没跑 → start (失败抛 ProbeVPSSetupFailed)
    ⑤ XRAY_DEFAULT_PORT (18440) 上没 socks5/freedom inbound → add + reload
       (失败抛 ProbeVPSSetupFailed)
    ⑥ 返回 ProbeVPSHandle(host, inbound_port=PROBE_TEST_PORT)

为什么独立模块不复用 XrayWorker:
  测试机不是业务资产, 不入 vps_record / vps_task / stage 锁, 端口可控,
  不需要让步算法 / 纳管 / 自启检查 / 任务退避. 见 ADR-0009 §决策 §1.

谁会调我:
  - main.py init-probe-vps 子命令
  - tools/init_probe_vps.py MCP 工具 (admin)
  - workers/ip_probe_worker.py 入口 (兜底自愈)

我用到的工具:
  - probe_vps.config (PROBE_TEST_PORT)
  - ssh.session.VPSSession (SSH 会话)
  - ssh.ops 异常类 (AuthFailedError / ConnectTimeoutError / ConnectRefusedError)
  - xray.manager.XrayManager (装/起/起验)
  - xray.config (读 / 上传 / 校验 config)
  - xray.service 异常类 (XrayError 及子类)

行为规约金标准: ADR-0009 §3 (本模块是独立工具不是 worker, 行为规约直接住 ADR)
"""

from __future__ import annotations

from dataclasses import dataclass

from config import XRAY_DEFAULT_PORT
from log import get_logger
from probe_vps.config import PROBE_TEST_PORT
from ssh.ops import (
    AuthFailedError,
    ConnectRefusedError,
    ConnectTimeoutError,
)
from ssh.session import VPSSession
from xray import config as xc
from xray.manager import XrayManager
from xray.service import XrayError


logger = get_logger("probe_vps.bootstrap")


# ============ 异常类 ============

class ProbeVPSError(Exception):
    """probe_vps 基类异常."""


class ProbeVPSUnreachable(ProbeVPSError):
    """SSH 连不上 (auth / timeout / refused).

    跟 IPProbeWorker.process 返回 status='probe_vps_unreachable' 对齐.
    """


class ProbeVPSSetupFailed(ProbeVPSError):
    """SSH 通但 xray 装 / 起 / 配 失败.

    映射到 IPProbeWorker 新增 status='probe_vps_not_ready', 跟 unreachable 区分:
    前者是测试机连不上, 后者是连上了但基础设施挂了.
    """


# ============ 句柄 ============

@dataclass(frozen=True)
class ProbeVPSHandle:
    """ensure_ready 成功返回的句柄.

    host: 测试 VPS 的 IP / hostname (调用方 entry["ip"] 透传).
    inbound_port: 测试 VPS 上 socks5/freedom inbound 监听端口
                  (固定 PROBE_TEST_PORT=19000, 不可配置).

    调用方一般只用它作为"成功"信号 — 后续要操作测试机就重新 VPSSession 连一次,
    不依赖本句柄持有连接 (bootstrap 跑完即关 SSH).
    """

    host: str
    inbound_port: int


# ============ inbound 占位检查 + 追加 (private, ADR-0009 §3 方案 b) ============

def _has_socks5_freedom_inbound(cfg: dict, port: int) -> bool:
    """检查 cfg 里是否已有 socks5(noauth) → freedom 落在指定端口的 inbound.

    判定标准 (ADR-0004 §决策 §3 直进直出本质):
      inbound 协议 == 'socks' && 路由到的 outbound 协议 == 'freedom' && port 一致.

    用途: ensure_ready ⑤ 步幂等检查, 已存在就跳过 add (跑 N 次结果一致).
    """
    inbounds = cfg.get("inbounds", []) or []
    outbounds = cfg.get("outbounds", []) or []
    rules = (cfg.get("routing", {}) or {}).get("rules", []) or []

    routing_map: dict[str, str] = {}
    for rule in rules:
        for tag in (rule.get("inboundTag", []) or []):
            routing_map[tag] = rule.get("outboundTag", "")

    outbound_by_tag = {ob.get("tag", ""): ob for ob in outbounds}

    for inb in inbounds:
        if inb.get("port") != port:
            continue
        if inb.get("protocol", "") != "socks":
            continue
        out_tag = routing_map.get(inb.get("tag", ""), "")
        outbound = outbound_by_tag.get(out_tag, {})
        if outbound.get("protocol", "") == "freedom":
            return True
    return False


def _append_socks5_freedom_inbound(cfg: dict, port: int) -> dict:
    """往 cfg 追加 socks5(noauth) → freedom inbound, 落在指定端口.

    空 / 缺关键字段的 cfg 用 build_vps_direct_config 起 baseline (自带
    default-direct@XRAY_DEFAULT_PORT socks→freedom), 然后看 baseline 是不是
    已经满足"指定端口有 socks5/freedom inbound"; 满足就直接 return baseline
    不再追加 (幂等, 避免 port=XRAY_DEFAULT_PORT 时跟 default-direct 双叠).
    不满足才追加一条 probe-direct inbound + routing 规则.

    跟 XrayWorker._append_default_direct 的 'default-direct' tag 区分:
    本函数追加分支用 'probe-direct' tag, 避免跟生产 18440 命名撞.
    """
    import copy

    if not cfg or "inbounds" not in cfg:
        new = xc.build_vps_direct_config()
    else:
        new = copy.deepcopy(cfg)

    new.setdefault("inbounds", [])
    new.setdefault("outbounds", [])
    new.setdefault("routing", {})
    new["routing"].setdefault("rules", [])

    if _has_socks5_freedom_inbound(new, port):
        return new

    probe_tag = "probe-direct"
    probe_inbound = {
        "tag": probe_tag,
        "port": port,
        "listen": "0.0.0.0",
        "protocol": "socks",
        "settings": {"auth": "noauth", "udp": True},
    }
    new["inbounds"].append(probe_inbound)

    # 复用现有 freedom outbound (build_vps_direct_config 留的 'direct' tag);
    # 若 cfg 已被改没 freedom outbound, 补一条 'probe-freedom'.
    freedom_tag = ""
    for ob in new["outbounds"]:
        if ob.get("protocol", "") == "freedom":
            freedom_tag = ob.get("tag", "")
            break
    if not freedom_tag:
        freedom_tag = "probe-freedom"
        new["outbounds"].append({"tag": freedom_tag, "protocol": "freedom"})

    new["routing"]["rules"].append({
        "type": "field",
        "inboundTag": [probe_tag],
        "outboundTag": freedom_tag,
    })
    return new


# ============ 主入口 ============

def ensure_ready(entry: dict) -> ProbeVPSHandle:
    """幂等装好测试机 + 起 xray + 留 PROBE_TEST_PORT inbound.

    入参 entry 跟 PROBE_VPS_POOL 元素同构 (ip / port / username / password 4 键),
    调用方负责选 slot 并处理 pool 空 / 越界 (本函数只处理一条).

    步骤见模块顶部 docstring + ADR-0009 §3.

    异常:
      ProbeVPSUnreachable - SSH 连不上 (auth / timeout / refused / 任何 ConnectionError)
      ProbeVPSSetupFailed - SSH 通但 xray install / start / 配 inbound 失败
    """
    host = entry["ip"]
    logger.info(
        "ensure_ready 启动: host=%s port=%s user=%s",
        host, entry["port"], entry["username"],
    )

    # ② SSH 连
    try:
        session = VPSSession(**entry).connect()
    except (AuthFailedError, ConnectTimeoutError, ConnectRefusedError) as exc:
        logger.warning(
            "ensure_ready: SSH 连不上 host=%s (%s: %s)",
            host, type(exc).__name__, exc,
        )
        raise ProbeVPSUnreachable(
            f"测试 VPS {host}:{entry['port']} SSH 连不上 ({type(exc).__name__}): {exc}"
        ) from exc
    except ConnectionError as exc:
        logger.warning(
            "ensure_ready: SSH 通用连接错误 host=%s (%s: %s)",
            host, type(exc).__name__, exc,
        )
        raise ProbeVPSUnreachable(
            f"测试 VPS {host}:{entry['port']} SSH 连不上 ({type(exc).__name__}): {exc}"
        ) from exc

    try:
        xm = XrayManager(session.client)

        # ③ xray 没装就装
        if not xm.is_installed():
            logger.info("ensure_ready: xray 未装, 跑 install (~30-60s)")
            try:
                xm.install()
            except XrayError as exc:
                raise ProbeVPSSetupFailed(
                    f"测试 VPS {host} xray install 失败: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        else:
            logger.info("ensure_ready: xray 已装 (version=%s)", xm.version())

        # ④ xray 没跑就起 (先确保 config 不空, 否则 start 会 exit=23)
        if xm.is_config_blank():
            logger.info("ensure_ready: config 空, 写默认 build_vps_direct_config")
            try:
                xm.write_default_config()
            except xc.ConfigWriteError as exc:
                raise ProbeVPSSetupFailed(
                    f"测试 VPS {host} 写默认 config 失败: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        if not xm.is_running():
            logger.info("ensure_ready: xray 未跑, 跑 start")
            try:
                xm.start()
            except XrayError as exc:
                raise ProbeVPSSetupFailed(
                    f"测试 VPS {host} xray start 失败: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
        else:
            logger.info("ensure_ready: xray 已跑")

        # ⑤ PROBE_TEST_PORT 没 socks5/freedom inbound → add + reload
        try:
            cfg = xc.read_config(session.client)
        except xc.ConfigReadError as exc:
            raise ProbeVPSSetupFailed(
                f"测试 VPS {host} 读 config 失败: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if _has_socks5_freedom_inbound(cfg, XRAY_DEFAULT_PORT):
            logger.info(
                "ensure_ready: port=%d 已有 socks5/freedom inbound, 跳过 add",
                XRAY_DEFAULT_PORT,
            )
        else:
            logger.info(
                "ensure_ready: port=%d 无 socks5/freedom inbound, add + reload",
                XRAY_DEFAULT_PORT,
            )
            try:
                new_cfg = _append_socks5_freedom_inbound(cfg, XRAY_DEFAULT_PORT)
                xm.upload_config(new_cfg)
                xm.validate_config()
                xm.reload()
            except (
                xc.ConfigWriteError,
                xc.ConfigValidationError,
                XrayError,
            ) as exc:
                raise ProbeVPSSetupFailed(
                    f"测试 VPS {host} add inbound (port={XRAY_DEFAULT_PORT}) 失败: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        # ⑥ 返回
        logger.info(
            "ensure_ready: 完成, host=%s inbound_port=%d",
            host, PROBE_TEST_PORT,
        )
        return ProbeVPSHandle(host=host, inbound_port=PROBE_TEST_PORT)
    finally:
        try:
            session.close()
        except Exception as exc:  # noqa: BLE001 — 关连接失败不影响主返回
            logger.warning(
                "ensure_ready: session.close 失败 (%s: %s)",
                type(exc).__name__, exc,
            )
