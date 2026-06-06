"""服务器本地防火墙操作。

主流三种防火墙系统的统一封装：
    firewalld  ← CentOS / RHEL 默认（systemctl is-active firewalld）
    ufw        ← Ubuntu / Debian 默认
    iptables   ← 老系统或自定义

策略：自动探测哪一种在用，调用对应命令；都没启用就跳过（无防火墙阻拦）。
"""

from __future__ import annotations

import paramiko

from ssh.ops import execute_command
from log import get_logger


logger = get_logger(__name__)


# 探测到的防火墙类型
FIREWALL_FIREWALLD = "firewalld"
FIREWALL_UFW = "ufw"
FIREWALL_NONE = "none"

FIREWALL_OPEN_FAILED_MESSAGE = (
    "服务器本地防火墙开放端口失败。"
    "可能原因：用户无 root 权限 / 防火墙服务异常 / 系统不支持。"
    "建议：手动登录服务器开放 18440-18450/tcp。"
)


class FirewallOpenError(RuntimeError):
    """服务器本地防火墙开放端口失败。"""


def detect_firewall(client: paramiko.SSHClient) -> str:
    """探测服务器使用哪种防火墙系统。

    返回 'firewalld' / 'ufw' / 'none'。
    """
    # firewalld 优先（CentOS / RHEL 主流）
    result = execute_command(client, "systemctl is-active firewalld 2>/dev/null")
    if result["stdout"].strip() == "active":
        return FIREWALL_FIREWALLD

    # ufw（Ubuntu / Debian 主流）
    result = execute_command(client, "ufw status 2>/dev/null | head -1")
    if "active" in result["stdout"].lower():
        return FIREWALL_UFW

    return FIREWALL_NONE


def open_tcp_port_range(
    client: paramiko.SSHClient, start_port: int, end_port: int
) -> str:
    """在服务器本地防火墙开放一段 TCP 端口（入方向）。

    返回探测到的防火墙类型（firewalld / ufw / none）。

    none 时不做任何动作（认为本地没有防火墙拦截，外网仍可能被云厂商安全组挡）。

    失败抛 FirewallOpenError，业务可选择忽略（best-effort 模式）。
    """
    fw = detect_firewall(client)
    logger.info(
        "open_tcp_port_range: start=%d end=%d → detected_fw=%s",
        start_port, end_port, fw,
    )

    if fw == FIREWALL_FIREWALLD:
        cmd_add = f"firewall-cmd --permanent --add-port={start_port}-{end_port}/tcp"
        cmd_reload = "firewall-cmd --reload"
        r1 = execute_command(client, cmd_add)
        r2 = execute_command(client, cmd_reload)
        if r1["exit_code"] != 0 or r2["exit_code"] != 0:
            raise FirewallOpenError(
                f"{FIREWALL_OPEN_FAILED_MESSAGE} (firewalld: add={r1['exit_code']} "
                f"reload={r2['exit_code']} stderr={r1['stderr'] or r2['stderr']})"
            )
        return fw

    if fw == FIREWALL_UFW:
        # ufw 端口范围语法用冒号：18440:18450
        cmd = f"ufw allow {start_port}:{end_port}/tcp"
        r = execute_command(client, cmd)
        if r["exit_code"] != 0:
            raise FirewallOpenError(
                f"{FIREWALL_OPEN_FAILED_MESSAGE} (ufw: exit={r['exit_code']} "
                f"stderr={r['stderr']})"
            )
        return fw

    # FIREWALL_NONE：服务器没启用防火墙，无需做事
    logger.info("open_tcp_port_range: fw=none → no-op (服务器未启用 firewalld/ufw)")
    return fw
