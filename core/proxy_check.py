"""从本机（运行此脚本的机器）发请求测试 socks5 代理是否可达。

跟 SSH 无关——这是为了验证「外部到 VPS 的网络路径 + 云服务商安全组」是否畅通。
被 VPS 安装业务用来确认 18440 是否真正可外部访问。
"""

from __future__ import annotations

import requests

from log import get_logger


logger = get_logger(__name__)


DEFAULT_TEST_URL = "https://api.ipify.org"
DEFAULT_TIMEOUT = 8

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
) -> dict:
    """从本机通过 socks5 代理发请求，验证代理是否可用。

    返回 {"ok": bool, "status_code": int|None, "body": str, "error": str|None}
    """
    proxies = {
        "http": f"socks5h://{proxy_ip}:{proxy_port}",
        "https": f"socks5h://{proxy_ip}:{proxy_port}",
    }
    logger.info("外部测试 socks5 %s:%s → %s", proxy_ip, proxy_port, test_url)
    try:
        r = requests.get(test_url, proxies=proxies, timeout=timeout)
        ok = r.status_code == 200
        body = (r.text or "").strip()[:200]
        if ok:
            logger.info("外部 socks5 通：%s:%s → 出口 IP=%s",
                        proxy_ip, proxy_port, body)
        else:
            logger.warning("外部 socks5 不通：%s:%s status=%s",
                           proxy_ip, proxy_port, r.status_code)
        return {"ok": ok, "status_code": r.status_code, "body": body, "error": None}
    except Exception as exc:
        logger.warning("外部 socks5 测试异常 %s:%s reason=%s",
                       proxy_ip, proxy_port, exc)
        return {
            "ok": False,
            "status_code": None,
            "body": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
