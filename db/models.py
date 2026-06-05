"""ORM 模型定义。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class XrayStatus:
    """xray 在 VPS 上的生命周期状态。"""

    NOT_INSTALLED = "not_installed"      # 初始状态
    INSTALLING = "installing"            # 安装中
    INSTALL_FAILED = "install_failed"    # 安装失败
    RUNNING = "running"                  # 服务运行中
    STOPPED = "stopped"                  # 已停止
    UNINSTALLED = "uninstalled"          # 已卸载


class VPSRecord(Base):
    """VPS 服务器 ORM 模型（一行 = 一台 VPS）。

    密码字段刻意命名为 password_encrypted，提醒读者：
    - 直接读取得到的是密文 bytes
    - 要拿到明文密码必须显式调用 get_password()
    """

    __tablename__ = "vps_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 服务商控制台域名（如 aliyun.com ），用于续费提醒与服务商维度归类
    provider_domain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    os_name: Mapped[str] = mapped_column(String(64), default="")
    os_version: Mapped[str] = mapped_column(String(32), default="")

    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ---------- xray 生命周期 ----------
    xray_status: Mapped[str] = mapped_column(
        String(32), default=XrayStatus.NOT_INSTALLED, nullable=False
    )
    xray_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    xray_installed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    xray_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    xray_status_message: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # 业务端口区间（18441-18450）内的可用端口数
    # 由 init_vps_xray 端口审计时更新；后续 IP 业务挑 VPS 时按这个降序排
    # 已被 OS 占用 / 在 COMMON_RESERVED 列表 / 已被 xray 配置绑定的端口都不算
    idle_port_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ---------- 业务方法 ----------

    def get_password(self) -> str:
        """获取明文密码。仅在需要建立 SSH 连接时调用。"""
        from core.security import decrypt_password
        return decrypt_password(self.password_encrypted)

    @classmethod
    def from_form(
        cls,
        ip: str,
        username: str,
        password: str,
        port: int = 22,
        os_name: str = "",
        os_version: str = "",
        expire_date: date | None = None,
        provider_domain: str = "",
    ) -> "VPSRecord":
        """从表单/入参构造 VPS 记录，密码在这里完成加密。"""
        from core.security import encrypt_password
        return cls(
            ip=ip,
            port=port,
            username=username,
            password_encrypted=encrypt_password(password),
            os_name=os_name,
            os_version=os_version,
            expire_date=expire_date,
            provider_domain=provider_domain,
        )

    def __repr__(self) -> str:
        # 注意：不打印任何密码字段
        return f"<VPSRecord id={self.id} ip={self.ip} user={self.username}>"


# ============================================================
# Proxy（端口绑定）
# ============================================================

class ProxyStatus:
    """proxy_record 的状态。固定大小事实表（一台 VPS 最多 10 条），
    用 status 字段反映「这条端口绑定当前在不在用」。"""

    USING = "using"      # 当前在用（IP 没过期）
    EXPIRED = "expired"  # 对应 IP 已过期，等被新 IP 顶替（行复用）


class ProxyRecord(Base):
    """VPS 端口绑定记录（事实表）。

    每行 = 一台 VPS 上一个端口（18441-18450 范围）目前挂着哪条上游代理。
    一台 VPS 最多 10 条（端口范围决定），固定大小：过期不删行，改 status='expired'
    等被下一条 IP 顶替（直接 update 该行）。

    数据来源（首期）：rgvps 流程的端口审计阶段，从 xray config 抠出已部署
    的客户端 inbound 绑定，逐条 from_extracted_binding 落库。
    未来 rgIP 业务部署新绑定时也走这张表。

    密码字段 inbound_pwd_encrypted 同 VPSRecord 约定：直接拿到的是密文 bytes，
    明文要走 get_inbound_pwd()。
    """

    __tablename__ = "proxy_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ---------- 关系 ----------
    vps_id: Mapped[int] = mapped_column(
        ForeignKey("vps_record.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    # VPS 上对外的端口（业务层约束在 PROXY_PORT_RANGE_START..END）
    vps_port: Mapped[int] = mapped_column(Integer, nullable=False)

    # ---------- 客户端连接侧（VPS_IP:vps_port 用什么协议/账密接入）----------
    # 一般是 'socks5'；将来如果要给客户端用 http 也支持
    protocol: Mapped[str] = mapped_column(String(16), default="socks5", nullable=False)
    inbound_user: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    inbound_pwd_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # ---------- 上游代理侧（流量从这条 binding 出去走的是哪个上游入口）----------
    # 上游代理的入口主机（域名或 IP），从 xray outbound.settings.servers[0].address 抠
    upstream_host: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # 上游代理的出口 IP（业务约定走 outbound["_meta"]["egress_ip"]）
    egress_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # 出口所在国家（同上，业务约定字段 outbound["_meta"]["egress_country"]）
    egress_country: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # ---------- 状态 ----------
    status: Mapped[str] = mapped_column(
        String(16), default=ProxyStatus.USING, nullable=False
    )

    # ---------- 审计 ----------
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # 同一 VPS 上同一端口不能重复挂——业务层 upsert 时按此唯一键命中
    __table_args__ = (
        UniqueConstraint("vps_id", "vps_port", name="uq_proxy_vps_port"),
    )

    # ---------- 业务方法 ----------

    def get_inbound_pwd(self) -> str:
        """获取客户端 inbound 的明文密码。仅在拼 xray inbound 配置时调用。"""
        from core.security import decrypt_password
        return decrypt_password(self.inbound_pwd_encrypted)

    @classmethod
    def from_extracted_binding(cls, vps_id: int, binding: dict) -> "ProxyRecord":
        """从 xray.config.extract_port_bindings() 的一项构造 ORM 记录。

        binding dict 形状参考 extract_port_bindings：必填 port / protocol /
        inbound_user / inbound_pwd / upstream_host；egress_ip / egress_country
        缺失则落空串。密码在这里加密。
        """
        from core.security import encrypt_password
        return cls(
            vps_id=vps_id,
            vps_port=binding["port"],
            protocol=binding.get("protocol", "socks5"),
            inbound_user=binding.get("inbound_user", ""),
            inbound_pwd_encrypted=encrypt_password(binding.get("inbound_pwd", "")),
            upstream_host=binding.get("upstream_host", ""),
            egress_ip=binding.get("egress_ip", ""),
            egress_country=binding.get("egress_country", ""),
        )

    def __repr__(self) -> str:
        # 不打印密码字段
        return (
            f"<ProxyRecord id={self.id} vps={self.vps_id}:{self.vps_port} "
            f"egress={self.egress_ip}@{self.egress_country or '?'} "
            f"status={self.status}>"
        )
