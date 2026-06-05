"""服务器端口探测工具。

提供查"VPS 上某范围内哪些端口被占用"的能力，给 rgIP 业务挑空闲端口用。
归在 core/ 而不是 xray/ 或 ip/——`ss -tlnp` 是通用 Linux 操作，跟任何具体
服务无关，未来 caddy / 别的业务也能复用。
"""

from __future__ import annotations

import paramiko

from core.ssh import execute_command
from log import get_logger


logger = get_logger(__name__)


PORT_PROBE_FAILED_MESSAGE = (
    "查询服务器端口占用失败（ss -tln 命令异常）。"
    "常见原因：① iproute2 未安装（极旧系统）；② 用户无执行权限。"
    "建议：登录服务器跑 `ss -tln` 直接看输出；若命令不存在，装 `iproute2` 包。"
)


class PortProbeError(RuntimeError):
    """端口探测失败（ss 命令异常 / 无法解析输出）。"""


def get_used_ports(
    client: paramiko.SSHClient,
    start_port: int,
    end_port: int,
) -> set[int]:
    """查询 VPS 上 [start_port, end_port] 范围内被占用的 TCP 监听端口。

    用 `ss -tln` 列出所有 LISTEN 状态的 TCP socket，提取 "Local Address:Port"
    列的端口数字，过滤进区间。支持 IPv4 / IPv6 / 通配地址的混合输出。

    返回区间内被占用的端口集合（int 集合）。如果该区间内没有占用，返回空集合。

    业务用法：rgIP 挑空闲端口时调 get_used_ports(client, 18441, 18450)，
    然后用 `set(range(18441, 18451)) - used` 拿到空闲端口集合。

    失败抛 PortProbeError（命令异常 / 解析失败）。
    """
    # -t TCP only, -l listening only, -n numeric (不反查 DNS / 服务名)
    result = execute_command(client, "ss -tln 2>/dev/null")
    if result["exit_code"] != 0:
        raise PortProbeError(
            f"{PORT_PROBE_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )

    used: set[int] = set()
    lines = result["stdout"].splitlines()
    # 跳过表头（第 1 行总是 "State Recv-Q Send-Q Local Address:Port ..."）
    for line in lines[1:]:
        parts = line.split()
        # 至少要有 4 列才有 "Local Address:Port"
        if len(parts) < 4:
            continue
        addr_port = parts[3]
        # 用 rsplit 防止 IPv6 形如 [::1]:8080 / [::]:443 误切
        if ":" not in addr_port:
            continue
        port_str = addr_port.rsplit(":", 1)[-1]
        try:
            port = int(port_str)
        except ValueError:
            # 解析不出来的行跳过（保守），但记一条 warning 提示运维
            logger.warning("get_used_ports 解析失败 line=%r", line)
            continue
        if start_port <= port <= end_port:
            used.add(port)

    return used
