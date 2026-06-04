"""ORM 模型定义。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import LargeBinary, String, Integer, Date, DateTime, func
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
    # 服务商控制台域名（如 aliyun.com / racknerd.com），用于续费提醒与服务商维度归类
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
        )

    def __repr__(self) -> str:
        # 注意：不打印任何密码字段
        return f"<VPSRecord id={self.id} ip={self.ip} user={self.username}>"
