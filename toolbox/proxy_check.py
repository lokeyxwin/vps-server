"""连通性测试工具 —— 两类协议各自封装成 Probe 类。

长期有两类连通测试场景:
  - 上游 IP / 纳管: 仍是 socks5         → Socks5Probe(封装现有逻辑, 行为不变)
  - 对外新部署节点: Shadowsocks(SS)     → ShadowsocksProbe(新增)

每个 Probe 类对外暴露两个方法:
  - test_internal: 在目标 VPS 内部验证 inbound 通不通, 顺手拿真实出口 IP
  - test_external: 从 worker 本机验证「外部到 VPS 的网络路径 + 云服务商安全组」是否放行

跟 SSH 的关系:
  - test_internal 走目标 VPS 的 SSH 会话内部执行(socks5 跑 curl / SS 起临时 xray)
  - test_external 从本机直连(socks5 走 requests 代理 / SS 测 TCP 端口可达)
"""

from __future__ import annotations

import base64
import json
import socket

import paramiko
import requests

import config
from log import get_logger
from ssh.ops import execute_command
from xray.config import XRAY_BIN


logger = get_logger(__name__)


DEFAULT_TEST_URL = "https://api.ipify.org"
DEFAULT_TIMEOUT = config.CONNECTIVITY_TEST_TIMEOUT

EXTERNAL_UNREACHABLE_MESSAGE = (
    "外部到 VPS 的代理不通。"
    "服务器内部 xray 正常但外部访问被拦——通常是云服务商安全策略组没放行。"
    "建议：登录云服务商控制台，在『安全组』规则添加："
    "入方向，TCP，对应节点端口，来源 0.0.0.0/0。"
)


def test_socks_proxy(
    proxy_ip: str,
    proxy_port: int,
    test_url: str = DEFAULT_TEST_URL,
    timeout: int = DEFAULT_TIMEOUT,
    user: str = "",
    pwd: str = "",
) -> dict:
    """从本机通过 socks5 代理发请求，验证代理是否可用。

    user/pwd 同时为空 = 免认证代理（rgvps 阶段测 18440 直出）
    user/pwd 至少一个非空 = 账密代理（rgIP 部署后测 18441+ 的客户端 inbound）

    返回 {"ok": bool, "status_code": int|None, "body": str, "error": str|None}
    """
    auth = f"{user}:{pwd}@" if (user or pwd) else ""
    proxies = {
        "http": f"socks5h://{auth}{proxy_ip}:{proxy_port}",
        "https": f"socks5h://{auth}{proxy_ip}:{proxy_port}",
    }
    # 日志不打 pwd，只标 with_auth 状态
    logger.info(
        "test_socks_proxy: target=%s:%s url=%s with_auth=%s → testing...",
        proxy_ip, proxy_port, test_url, bool(user or pwd),
    )
    try:
        r = requests.get(test_url, proxies=proxies, timeout=timeout)
        ok = r.status_code == 200
        body = (r.text or "").strip()[:200]
        if ok:
            logger.info(
                "test_socks_proxy: target=%s:%s → ok=True http=%s egress=%s",
                proxy_ip, proxy_port, r.status_code, body,
            )
        else:
            logger.warning(
                "test_socks_proxy: target=%s:%s → ok=False http=%s",
                proxy_ip, proxy_port, r.status_code,
            )
        return {"ok": ok, "status_code": r.status_code, "body": body, "error": None}
    except Exception as exc:  # noqa: BLE001 — 兜底未分类异常并转换为业务错误
        logger.warning(
            "test_socks_proxy: target=%s:%s → error=%s (%s)",
            proxy_ip, proxy_port, type(exc).__name__, exc,
        )
        return {
            "ok": False,
            "status_code": None,
            "body": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def test_internal(
    client: paramiko.SSHClient,
    port: int,
    user: str = "",
    pwd: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bool, str]:
    """⭐ 内 ping —— 在 VPS 内部 SSH 跑 curl 测 inbound 通不通, 顺手拿出口 IP。

    返回 (ok, egress_ip):
        ok       : True/False, inbound 通不通
        egress_ip: 通时是 curl body (即 api.ipify.org 看到的真实出口 IP);
                   不通时返回 ""。

    用 socks5 走 127.0.0.1:port 发请求, 通 = 服务器自己连自己通。
    XrayWorker 纳管别人挂的代理出口时, 通的同时反推真实出口 IP 入 ip_record。
    只需要 bool 的调用方 `ok, _ = test_internal(...)` 忽略第二项即可。

    内部委托给 xray.service.test_internal_socks 拿完整 dict, 取 ok + body。
    """
    from xray.service import test_internal_socks  # noqa: PLC0415 — 局部 import 避免循环依赖
    result = test_internal_socks(
        client=client,
        port=port,
        user=user,
        pwd=pwd,
        timeout=timeout,
    )
    ok = result.get("ok", False)
    egress_ip = (result.get("body") or "").strip() if ok else ""
    return ok, egress_ip


def test_external(
    host: str,
    port: int,
    user: str = "",
    pwd: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> bool:
    """⭐ 外 ping —— 从本机通过 socks5 代理发请求测远程 inbound 通不通,返回 True/False。

    用 socks5 走 host:port 从 worker 本机发请求,通 = 外部到 VPS 网络路径 + 防火墙都放行。
    后续 ProxyDeployWorker 部署新代理出口后用,验证客户端能从外部连上。

    内部委托给 test_socks_proxy,只取 result["ok"]。
    """
    result = test_socks_proxy(
        proxy_ip=host,
        proxy_port=port,
        user=user,
        pwd=pwd,
        timeout=timeout,
    )
    return result.get("ok", False)


# ============================================================
# Socks5Probe —— 封装现有 socks5 内/外 ping 逻辑(行为零变化)
# ============================================================

class Socks5Probe:
    """socks5 连通测试器。用于上游 IP 校验 + 纳管(later)。

    方法只是把上面的模块级函数 test_internal / test_external 搬进类做委托,
    逻辑一字不改 —— 行为与原函数完全一致。
    """

    def test_internal(
        self,
        client: paramiko.SSHClient,
        port: int,
        user: str = "",
        pwd: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> tuple[bool, str]:
        """内 ping(目标 VPS 内部): 委托模块级 test_internal, 返回 (ok, egress_ip)。"""
        return test_internal(client, port, user=user, pwd=pwd, timeout=timeout)

    def test_external(
        self,
        host: str,
        port: int,
        user: str = "",
        pwd: str = "",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> bool:
        """外 ping(worker 本机): 委托模块级 test_external, 返回 True/False。"""
        return test_external(host, port, user=user, pwd=pwd, timeout=timeout)


# ============================================================
# ShadowsocksProbe —— SS 节点连通测试(新增)
# ============================================================

# 临时 xray 探测实例用的固定文件 / 端口约定。
# 内 ping 在目标 VPS 起一个临时 xray: socks-in(本机临时口) → ss-out(连本机 SS 端口)。
_SS_PROBE_SOCKS_PORT = 19010          # 临时 socks 入口(跟生产 18441+ / 测试 19000 隔离)
_SS_PROBE_CONFIG_PATH = "/tmp/_ss_probe.json"
_SS_PROBE_PID_PATH = "/tmp/_ss_probe.pid"
_SS_PROBE_OUT_PATH = "/tmp/_ss_probe_curl.out"


class ShadowsocksProbe:
    """Shadowsocks 连通测试器。给 ProxyDeployWorker 部署 SS 节点后验证。

    内 ping: 目标 VPS 本机起临时 xray 实例端到端验 SS(握手+加密+密码+上游出口);
    外 ping: worker 本机 TCP 端口可达测(SS 跑 TCP, 端口能连上 = 安全组放行), 不拉核心。
    """

    def test_internal(
        self,
        client: paramiko.SSHClient,
        port: int,
        method: str,
        password: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> tuple[bool, str]:
        """⭐ 内 ping —— 目标 VPS 本机起临时 xray 端到端测 SS, 拿真实出口 IP。

        临时 xray 形态: socks-in(127.0.0.1:_SS_PROBE_SOCKS_PORT, noauth)
                        → ss-out(连 127.0.0.1:port, method/password)。
        curl --socks5 临时口 → api.ipify.org 拿出口 IP。

        返回 (ok, egress_ip):
            ok       : SS 端到端是否通
            egress_ip: 通时是 curl body(真实出口 IP); 不通返回 ""。

        ⚠️ try/finally 兜底 kill 临时进程 + 删临时配置, 绝不碰主 xray 的
           /usr/local/etc/xray/config.json。
        """
        probe_config = self._build_probe_config(port, method, password)
        logger.info(
            "ShadowsocksProbe.test_internal: ss_port=%s method=%s socks_port=%s → testing...",
            port, method, _SS_PROBE_SOCKS_PORT,
        )
        try:
            self._write_probe_config(client, probe_config)
            self._start_probe_xray(client)
            ok, egress = self._curl_through_probe(client, timeout)
            if ok:
                logger.info(
                    "ShadowsocksProbe.test_internal: ss_port=%s → ok=True egress=%s",
                    port, egress,
                )
            else:
                logger.warning(
                    "ShadowsocksProbe.test_internal: ss_port=%s → ok=False", port,
                )
            return ok, egress
        except Exception as exc:  # noqa: BLE001 — 起不来/curl 不通统一算「不通」
            logger.warning(
                "ShadowsocksProbe.test_internal: ss_port=%s → error=%s (%s)",
                port, type(exc).__name__, exc,
            )
            return False, ""
        finally:
            self._cleanup_probe(client)

    def test_external(
        self,
        host: str,
        port: int,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> bool:
        """⭐ 外 ping —— worker 本机 TCP 端口可达测, 不拉任何核心。

        socket.create_connection((host, port)) 成功即 True(TCP 可达 = 安全组放行)。
        SS 跑在 TCP 上, 外部能连上端口 = 云服务商安全组放行了;
        配合内 ping(已端到端验透 SS 协议层), 逻辑上闭环。
        """
        logger.info(
            "ShadowsocksProbe.test_external: target=%s:%s → tcp probing...",
            host, port,
        )
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
            logger.info(
                "ShadowsocksProbe.test_external: target=%s:%s → ok=True (tcp reachable)",
                host, port,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — 拒绝/超时/解析失败统一算「不通」
            logger.warning(
                "ShadowsocksProbe.test_external: target=%s:%s → ok=False (%s: %s)",
                host, port, type(exc).__name__, exc,
            )
            return False

    # ============ 内部小工具(私有, 只在本类内用) ============

    @staticmethod
    def _build_probe_config(port: int, method: str, password: str) -> dict:
        """拼临时 xray 探测配置: socks-in(noauth) → ss-out(连本机 SS 端口)。"""
        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "ss-probe-in",
                    "listen": "127.0.0.1",
                    "port": _SS_PROBE_SOCKS_PORT,
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": False},
                },
            ],
            "outbounds": [
                {
                    "tag": "ss-probe-out",
                    "protocol": "shadowsocks",
                    "settings": {
                        "servers": [
                            {
                                "address": "127.0.0.1",
                                "port": port,
                                "method": method,
                                "password": password,
                            },
                        ],
                    },
                },
            ],
            "routing": {
                "rules": [
                    {
                        "type": "field",
                        "inboundTag": ["ss-probe-in"],
                        "outboundTag": "ss-probe-out",
                    },
                ],
            },
        }

    @staticmethod
    def _write_probe_config(client: paramiko.SSHClient, probe_config: dict) -> None:
        """把临时配置写到 _SS_PROBE_CONFIG_PATH(独立路径, 不碰主 config)。

        Python 端先 base64 编码 config_json 再远程 `base64 -d` 落盘, 规避 heredoc
        对 password 里特殊字符/换行的注入与截断风险(密码是部署随机串, 仍稳妥处理)。
        """
        config_json = json.dumps(probe_config)
        config_b64 = base64.b64encode(config_json.encode("utf-8")).decode("ascii")
        cmd = f"echo {config_b64} | base64 -d > {_SS_PROBE_CONFIG_PATH}"
        result = execute_command(client, cmd)
        if result["exit_code"] != 0:
            raise RuntimeError(
                f"写临时探测配置失败: exit={result['exit_code']} "
                f"stderr={result['stderr'][:120]}"
            )

    @staticmethod
    def _start_probe_xray(client: paramiko.SSHClient) -> None:
        """后台起临时 xray 实例, 记 pid 到 _SS_PROBE_PID_PATH。

        后台化(`&`)本身必返 exit_code=0, 配置错时 xray 起来就退也检测不到。
        sleep 1 后用 `kill -0 <pid>` 探活: 进程已死则整条命令 exit_code != 0,
        被下面的 check 捕获抛错。
        """
        cmd = (
            f"nohup {XRAY_BIN} run -c {_SS_PROBE_CONFIG_PATH} "
            f">/dev/null 2>&1 & echo $! > {_SS_PROBE_PID_PATH} ; "
            f"sleep 1 ; kill -0 $(cat {_SS_PROBE_PID_PATH}) 2>/dev/null"
        )
        result = execute_command(client, cmd)
        if result["exit_code"] != 0:
            raise RuntimeError(
                f"起临时 xray 探测实例失败: exit={result['exit_code']} "
                f"stderr={result['stderr'][:120]}"
            )

    @staticmethod
    def _curl_through_probe(
        client: paramiko.SSHClient, timeout: int,
    ) -> tuple[bool, str]:
        """curl --socks5-hostname 临时口 → 拿 egress IP。解析复用 __HTTPCODE__/__BODY__ 协议。

        用 --socks5-hostname(= socks5h, 远端解析 DNS), 跟原 socks5 探测
        (test_socks_proxy 的 socks5h://)语义对齐, 避免 VPS 本地 DNS 假阴性。
        """
        cmd = (
            f"curl --socks5-hostname 127.0.0.1:{_SS_PROBE_SOCKS_PORT} -m {timeout} -s "
            f"-w '__HTTPCODE__%{{http_code}}' -o {_SS_PROBE_OUT_PATH} "
            f"{DEFAULT_TEST_URL} 2>&1 ; "
            f"echo '__BODY__' ; cat {_SS_PROBE_OUT_PATH} 2>/dev/null ; "
            f"rm -f {_SS_PROBE_OUT_PATH}"
        )
        result = execute_command(client, cmd, timeout=timeout + 5)
        out = result["stdout"]

        http_code = None
        if "__HTTPCODE__" in out:
            try:
                code_part = out.split("__HTTPCODE__", 1)[1].split("__BODY__", 1)[0]
                http_code = int(code_part.strip())
            except (ValueError, IndexError):
                http_code = None

        body = ""
        if "__BODY__" in out:
            body = out.split("__BODY__", 1)[1].strip()[:200]

        ok = http_code == 200
        return ok, (body if ok else "")

    @staticmethod
    def _cleanup_probe(client: paramiko.SSHClient) -> None:
        """kill 临时 xray 进程 + 删临时配置/pid 文件。失败不抛(finally 已兜底)。"""
        cmd = (
            f"[ -f {_SS_PROBE_PID_PATH} ] && kill $(cat {_SS_PROBE_PID_PATH}) 2>/dev/null ; "
            f"rm -f {_SS_PROBE_CONFIG_PATH} {_SS_PROBE_PID_PATH} {_SS_PROBE_OUT_PATH}"
        )
        try:
            execute_command(client, cmd)
            logger.info("ShadowsocksProbe._cleanup_probe: 临时进程+配置已清理")
        except Exception as exc:  # noqa: BLE001 — cleanup 失败不影响探测结果
            logger.warning(
                "ShadowsocksProbe._cleanup_probe failed: %s: %s",
                type(exc).__name__, exc,
            )
