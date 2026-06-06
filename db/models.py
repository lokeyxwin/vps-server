"""ORM 模型定义。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class VPSStage:
    """VPS 占用状态机（2 值，spec v4 拍板）。

    谁推进:
      SSHWorker 入库时    → 永远写 CONNECTABLE（spec v4 §5 不变量）
      抢到这台机的工人     → 写 RUNNING 锁住（XrayWorker / 未来巡检等）
      工人干完释放         → 改回 CONNECTABLE
      工人失败            → 保持 RUNNING 锁住等人介入（spec v4 Q2 拍板）

    业务含义:
      CONNECTABLE = 此刻没工人在用 + 验证过能连，可被任意工人抢
      RUNNING     = 有任意工人正在用，别的工人挑机时跳过
    """

    CONNECTABLE = "connectable"
    RUNNING = "running"


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
    # port 业务层必填强制（spec v4 §2），ORM 不兜底 default
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    os_name: Mapped[str] = mapped_column(String(64), default="")
    os_version: Mapped[str] = mapped_column(String(32), default="")

    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 1=可用 / 0=过期；巡检模块维护，业务层挑 VPS 时读这个 + 兜底比 expire_date
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # ---------- 占用状态机（spec v4：2 值，SSHWorker 写 connectable，抢机工人写 running）----------
    stage: Mapped[str] = mapped_column(
        String(32), default=VPSStage.CONNECTABLE, nullable=False
    )
    # xray_version：SSHWorker 永远不写（留空字符串），XrayWorker 第一次干完写
    xray_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    xray_installed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    xray_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # XrayWorker 纳管时记录已被 xray 绑定的端口数（spec v4：原 idle_port_count 改名）
    used_port_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

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
        from toolbox.security import decrypt_password
        return decrypt_password(self.password_encrypted)

    @classmethod
    def from_form(
        cls,
        ip: str,
        username: str,
        password: str,
        port: int,
        os_name: str = "",
        os_version: str = "",
        expire_date: date | None = None,
        provider_domain: str = "",
    ) -> "VPSRecord":
        """从表单/入参构造 VPS 记录，密码在这里完成加密。

        port 必填（spec v4 §2：业务层强制，ORM 不兜底 default=22）。
        os_name / os_version 可空（spec v4 §6：SSH 上去读不到留空入库）。
        """
        from toolbox.security import encrypt_password
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
        # 注意：不打印任何密码字段；stage 暴露给排障
        return f"<VPSRecord id={self.id} ip={self.ip} stage={self.stage}>"


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

    # rgIP 业务新增的 binding 必填；rgvps 端口审计从 xray config 反推的 binding 暂时填 None
    # （巡检模块未来按 egress_ip 回填）
    ip_id: Mapped[int | None] = mapped_column(
        ForeignKey("ip_record.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )

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
        from toolbox.security import decrypt_password
        return decrypt_password(self.inbound_pwd_encrypted)

    @classmethod
    def from_extracted_binding(cls, vps_id: int, binding: dict) -> "ProxyRecord":
        """从 xray.config.extract_port_bindings() 的一项构造 ORM 记录。

        binding dict 形状参考 extract_port_bindings：必填 port / protocol /
        inbound_user / inbound_pwd / upstream_host；egress_ip / egress_country
        缺失则落空串。密码在这里加密。

        ip_id 留 None：从 xray config 反推的 binding 没法直接对应 ip_record；
        后续巡检模块会按 egress_ip 字符串回填。
        """
        from toolbox.security import encrypt_password
        return cls(
            vps_id=vps_id,
            vps_port=binding["port"],
            ip_id=None,
            protocol=binding.get("protocol", "socks5"),
            inbound_user=binding.get("inbound_user", ""),
            inbound_pwd_encrypted=encrypt_password(binding.get("inbound_pwd", "")),
            upstream_host=binding.get("upstream_host", ""),
            egress_ip=binding.get("egress_ip", ""),
            egress_country=binding.get("egress_country", ""),
        )

    @classmethod
    def from_new_deployment(
        cls,
        *,
        vps_id: int,
        vps_port: int,
        ip_id: int,
        inbound_user: str,
        inbound_pwd: str,
        upstream_host: str,
        egress_ip: str,
        egress_country: str = "",
        protocol: str = "socks5",
    ) -> "ProxyRecord":
        """rgIP 业务部署新 binding 时构造 ORM 记录。

        与 from_extracted_binding 的区别：
        - ip_id 必填（业务持有 IPRecord.id）
        - 入参显式而非 dict（业务清楚自己手里有啥）
        - inbound_pwd 在这里加密
        """
        from toolbox.security import encrypt_password
        return cls(
            vps_id=vps_id,
            vps_port=vps_port,
            ip_id=ip_id,
            protocol=protocol,
            inbound_user=inbound_user,
            inbound_pwd_encrypted=encrypt_password(inbound_pwd),
            upstream_host=upstream_host,
            egress_ip=egress_ip,
            egress_country=egress_country,
            status=ProxyStatus.USING,
        )

    def __repr__(self) -> str:
        # 不打印密码字段
        return (
            f"<ProxyRecord id={self.id} vps={self.vps_id}:{self.vps_port} "
            f"egress={self.egress_ip}@{self.egress_country or '?'} "
            f"status={self.status}>"
        )


# ============================================================
# IP（上游代理凭据）
# ============================================================

class IPProtocol:
    """上游代理协议常量。"""

    SOCKS5 = "socks5"
    HTTP = "http"


class IPRecord(Base):
    """上游代理 ORM 模型（一行 = 一条 egress_ip）。

    egress_ip 是业务身份键（唯一），同一入口 entry_host 可对应多条 egress
    （云服务商常给一个域名入口、分多条出口 IP），所以 entry_host 不做 unique。

    地区字段 (country_code / country_name / city / region_name) 来自 geoip 权威
    （toolbox.geoip.lookup_egress 返回值，由业务层 rgIP 流程内 ping 通过后落库）。
    用户填的 user_label 是自由备注，不参与查询匹配。

    密码字段 password_encrypted 同 VPSRecord 约定：直接拿到的是密文 bytes，
    明文要走 get_password()。
    """

    __tablename__ = "ip_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ---------- 上游入口（云服务商控制台凭据）----------
    entry_host: Mapped[str] = mapped_column(String(255), nullable=False)
    entry_port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False)

    # ---------- 出口（业务身份键）----------
    egress_ip: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # ---------- 地区身份（geoip 权威）----------
    # country_code 是查询主键；user MCP 的"美国/SG"等查询走中英文映射 → country_code
    country_code: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    country_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    city: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    region_name: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # ---------- 元信息 ----------
    provider_domain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    expire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 1=可用 / 0=过期；巡检模块维护，业务读这个 + 兜底比 expire_date
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 用户自定义备注，不参与查询匹配
    user_label: Mapped[str] = mapped_column(String(64), default="", nullable=False)

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
        """获取上游代理明文密码。仅在拼 xray outbound 配置时调用。"""
        from toolbox.security import decrypt_password
        return decrypt_password(self.password_encrypted)

    @classmethod
    def from_form(
        cls,
        *,
        entry_host: str,
        entry_port: int,
        username: str,
        password: str,
        protocol: str,
        egress_ip: str,
        provider_domain: str = "",
        expire_date: date | None = None,
        user_label: str = "",
        geo: dict | None = None,
    ) -> "IPRecord":
        """从表单/入参构造 IP 记录。

        password 在这里完成加密。
        geo 是 toolbox.geoip.lookup_egress 的返回 dict（成功时含 country_code/
        country_name/city/region_name；失败兜底 None → 全落空串）。
        """
        from toolbox.security import encrypt_password
        geo = geo or {}
        return cls(
            entry_host=entry_host,
            entry_port=entry_port,
            username=username,
            password_encrypted=encrypt_password(password),
            protocol=protocol,
            egress_ip=egress_ip,
            country_code=geo.get("country_code", ""),
            country_name=geo.get("country_name", ""),
            city=geo.get("city", ""),
            region_name=geo.get("region_name", ""),
            provider_domain=provider_domain,
            expire_date=expire_date,
            user_label=user_label,
        )

    def __repr__(self) -> str:
        # 不打印 username / password 等敏感字段
        return (
            f"<IPRecord id={self.id} egress={self.egress_ip} "
            f"country={self.country_code or '?'} city={self.city or '?'} "
            f"active={self.is_active}>"
        )


# ============================================================
# Task（异步任务表 —— worker 接力媒介）
# ============================================================

class TaskStatus:
    """task 表通用状态机（vps_task / 未来 ip_task 共用）。

    v4 拍板：只 4 个值。重试 / 退避 / 熔断细节藏在工人内部。

    谁推进:
      pending → in_progress    : worker 抢到锁
      in_progress → done       : 干完成功
      in_progress → failed     : 工人内部重试 N 次仍失败（终态）

    关键约束:
      没有 pending_retry：工人在 in_progress 期间内部循环重试
                          （用 retry_count + next_run_at 自管退避）
      没有 circuit_broken：retry_count >= N 直接标 failed
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class VPSTask(Base):
    """VPS 装机 / 纳管类任务。XrayWorker 消费。

    每条 = 一个 "装 xray / 启停 xray / 纳管" 活儿。
    SSHWorker 入库 VPS 时建一条 pending，XrayWorker 扫表领活儿。

    锁粒度 = task。worker 抢到 task = 抢到 task.vps_id 那台机的操作权
    （CLAUDE.local.md §4：一台 VPS 同时只能被 1 个 worker 持锁）。

    retry_count / next_run_at / worker_id / locked_until 这 4 个字段
    **都给 XrayWorker 内部用**（自管退避 + 软锁），不驱动 status 状态机。
    """

    __tablename__ = "vps_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    vps_id: Mapped[int] = mapped_column(
        ForeignKey("vps_record.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    status: Mapped[str] = mapped_column(
        String(16), default=TaskStatus.PENDING, nullable=False
    )
    # XrayWorker 内部用：失败几次了，>= N 时升 failed 终态
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # XrayWorker 内部用：下次什么时候再试（退避时间），扫表条件
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # 最近一次失败的错误代号 / 人话原因（spec v4 §5：错误只住任务表）
    last_error_code: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    last_error_msg: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # 谁在锁着这条 + 锁过期时间（软锁，过期自动释放）
    worker_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # worker 扫表领活儿（每秒发生）：按 status='pending' + next_run_at <= now 查
        Index("ix_vps_task_status_next_run", "status", "next_run_at"),
        # 查"VPS#X 当前有没有任务在跑"（业务挑机时双表 join）
        Index("ix_vps_task_vps_status", "vps_id", "status"),
    )

    def __repr__(self) -> str:
        # 不打印 last_error_msg（长字段）
        return (
            f"<VPSTask id={self.id} vps={self.vps_id} "
            f"status={self.status} retry={self.retry_count}>"
        )
