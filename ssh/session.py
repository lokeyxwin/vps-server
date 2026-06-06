"""VPSSession 类：封装一次 SSH 会话的生命周期。

跟 ssh/ops.py 同层（基础设施），不感知 DB、不感知业务。
所有领域的 Manager 类（XrayManager / 未来 IP/Proxy）通过它拿到底层 client。
"""

from __future__ import annotations

import paramiko

from toolbox.ports import (
    compute_available_ports,
    get_used_ports,
    is_port_free,
)
from ssh.ops import (
    connect_server,
    close_server,
    execute_command,
    get_system_info,
)


NOT_CONNECTED_MESSAGE = "尚未建立连接，请先调用 connect() 或使用 with 上下文"


class VPSSession:
    """对一台 VPS 的 SSH 会话对象。

    用法 A —— 显式管理：
        s = VPSSession(ip, user, pwd, port)
        s.connect()
        s.execute("ls")
        s.close()

    用法 B —— 上下文管理（推荐）：
        with VPSSession(ip, user, pwd, port) as s:
            info = s.get_system_info()

    用法 C —— 从 ORM 记录构造：
        with VPSSession.from_record(record) as s:
            ...

    用法 D —— 给领域 Manager 用底层 client：
        with VPSSession(...) as s:
            xm = XrayManager(s.client)
            xm.ensure_installed_and_running()
    """

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        port: int = 22,
    ) -> None:
        self.ip = ip
        self.username = username
        self.password = password
        self.port = port
        self._client: paramiko.SSHClient | None = None

    @classmethod
    def from_record(cls, record) -> "VPSSession":
        """从 ORM 记录构造会话。解密在这里发生，业务层无感知。

        record 需具备：ip / username / port 属性，以及 get_password() 方法。
        用鸭子类型而非显式 import VPSRecord，避免反向依赖 db 层。
        """
        return cls(
            ip=record.ip,
            username=record.username,
            password=record.get_password(),
            port=record.port,
        )

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> paramiko.SSHClient:
        """暴露底层 paramiko client，供领域 Manager 使用。"""
        self._ensure_connected()
        return self._client

    def connect(self) -> "VPSSession":
        if self._client is None:
            self._client = connect_server(
                self.ip, self.username, self.password, self.port
            )
        return self

    def execute(self, command: str, timeout: int = 30) -> dict:
        self._ensure_connected()
        return execute_command(self._client, command, timeout=timeout)

    def get_system_info(self) -> dict:
        self._ensure_connected()
        return get_system_info(self._client)

    # -------- 端口探测（包装 toolbox.ports，业务调起来不用再传 client）--------

    def is_port_free(self, port: int) -> bool:
        """查单个端口在 VPS 上是否空闲。"""
        self._ensure_connected()
        return is_port_free(self._client, port)

    def get_used_ports(self, start_port: int, end_port: int) -> set[int]:
        """查 VPS 上 [start, end] 区间内被占用的 TCP 监听端口。"""
        self._ensure_connected()
        return get_used_ports(self._client, start_port, end_port)

    def get_available_ports(
        self,
        start_port: int,
        end_port: int,
        exclude: set[int] | frozenset[int] | None = None,
    ) -> set[int]:
        """一次性算出区间内可用端口集合（已扣掉 OS 占用 + exclude）。

        exclude 不传走 toolbox.ports.COMMON_RESERVED_PORTS（22/443/3306 等）。
        业务用法：vps.get_available_ports(18441, 18450) 直接拿可用集合。
        """
        used = self.get_used_ports(start_port, end_port)
        return compute_available_ports(used, start_port, end_port, exclude)

    def close(self) -> None:
        if self._client is not None:
            close_server(self._client)
            self._client = None

    def _ensure_connected(self) -> None:
        if self._client is None:
            raise RuntimeError(NOT_CONNECTED_MESSAGE)

    def __enter__(self) -> "VPSSession":
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
