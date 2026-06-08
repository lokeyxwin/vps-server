"""从本机（运行此脚本的机器）发请求测试 socks5 代理是否可达。

跟 SSH 无关——这是为了验证「外部到 VPS 的网络路径 + 云服务商安全组」是否畅通。
被 VPS 安装业务用来确认 18440 是否真正可外部访问。
"""

from __future__ import annotations

import paramiko
import requests

import config
from log import get_logger


logger = get_logger(__name__)


DEFAULT_TEST_URL = config.CONNECTIVITY_TEST_URL
DEFAULT_TIMEOUT = config.CONNECTIVITY_TEST_TIMEOUT

EXTERNAL_UNREACHABLE_MESSAGE = (
    "外部到 VPS 的 socks5 代理不通。"
    "服务器内部 xray 正常但外部访问被拦——通常是云服务商安全策略组没放行。"
    "建议：登录云服务商控制台，在『安全组』规则添加："
    "入方向，TCP，端口范围 18440-18450，来源 0.0.0.0/0。"
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
) -> bool:
    """⭐ 内 ping —— 在 VPS 内部 SSH 跑 curl 测 inbound 通不通,返回 True/False。

    用 socks5 走 127.0.0.1:port 发请求,通 = 服务器自己连自己通。
    XrayWorker 统一收尾对每条"代理出口"内 ping,通则纳管入库,不通则 remove 三件套。

    内部委托给 xray.service.test_internal_socks,只取 result["ok"]。
    """
    from xray.service import test_internal_socks  # noqa: PLC0415 — 局部 import 避免循环依赖
    result = test_internal_socks(
        client=client,
        port=port,
        user=user,
        pwd=pwd,
        timeout=timeout,
    )
    return result.get("ok", False)


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
