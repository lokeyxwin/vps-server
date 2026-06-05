"""注册 VPS 业务：一站式 provisioning。

流程：
    ① 查重（DB 已有 IP → duplicate）
    ② SSH 测连 + 采集系统信息（4 种错误细分：auth/timeout/refused/failed）
    ③ 入库（密码加密、xray_status='not_installed' 默认）
    ④ 调用 init_vps_xray 业务把 xray 也搞定（同一函数复用）
    ⑤ 返回最终 status

入库后 xray 全流程也会跑：装 → 起 → 自启。
"""

from datetime import date

from db import VPSRecord, session_scope
from log import get_logger
from services.vps_init import init_vps_xray
from core import (
    AuthFailedError,
    ConnectTimeoutError,
    ConnectRefusedError,
    VPSSession,
)


logger = get_logger(__name__)


def register_vps(
    ip: str,
    username: str,
    password: str,
    port: int = 22,
    expire_date: date | None = None,
    provider_domain: str = "",
) -> dict:
    """注册一台 VPS + 顺手装好 xray + 启用服务。

    返回 status 枚举：
        ok             - 成功（注册 + xray 全流程）
        ok_xray_partial - 注册成功但 xray 部分失败（看 xray_status 字段）
        duplicate      - 该 IP 已在表中
        auth_failed / timeout / refused / failed - SSH 连接失败

    provider_domain：服务商控制台域名（如 aliyun.com / linode.com），
    用于后续按服务商维度做续费提醒；未提供时存空字符串，不影响主流程。
    """
    logger.info(
        "开始登记 VPS：ip=%s 账号=%s 端口=%s 服务商=%s",
        ip, username, port, provider_domain or "(未填)",
    )

    # ① 查重
    with session_scope() as session:
        if session.query(VPSRecord).filter_by(ip=ip).first() is not None:
            logger.info("数据库里已经有这台 VPS 了，跳过登记：ip=%s", ip)
            return {"status": "duplicate", "message": f"IP {ip} 已存在数据库"}

    # ② SSH 测连 + 采集系统信息
    logger.info("数据库里还没这台，准备 SSH 上去看看情况")
    try:
        with VPSSession(ip, username, password, port) as vps:
            info = vps.get_system_info()
    except AuthFailedError as exc:
        logger.warning("登录失败：账号或密码不对（ip=%s）", ip)
        return {"status": "auth_failed", "message": str(exc)}
    except ConnectTimeoutError as exc:
        logger.warning("连接超时：可能 SSH 端口被防火墙挡了（ip=%s）", ip)
        return {"status": "timeout", "message": str(exc)}
    except ConnectRefusedError as exc:
        logger.warning("连接被拒：SSH 端口没开或端口号不对（ip=%s）", ip)
        return {"status": "refused", "message": str(exc)}
    except ConnectionError as exc:
        logger.warning("连接失败：未知错误（ip=%s）", ip)
        return {"status": "failed", "message": str(exc)}

    # ③ 基础信息入库
    with session_scope() as session:
        record = VPSRecord.from_form(
            ip=ip,
            username=username,
            password=password,
            port=port,
            os_name=info["os_name"],
            os_version=info["os_version"],
            expire_date=expire_date,
            provider_domain=provider_domain,
        )
        session.add(record)

    logger.info(
        "VPS 基础信息已存入数据库：ip=%s 系统是 %s %s，接下来去装 xray",
        ip, info["os_name"], info["os_version"],
    )

    # ④ 链式调用 init_vps_xray —— 复用业务，避免逻辑重复
    xray_result = init_vps_xray(ip)

    # ⑤ 合成最终返回
    if xray_result["status"] in ("ok", "imported", "already_running"):
        logger.info(
            "VPS 完整登记完成：ip=%s 系统是 %s %s，xray 状态=%s",
            ip, info["os_name"], info["os_version"], xray_result["status"],
        )
        return {
            "status": "ok",
            "ip": ip,
            "os": f"{info['os_name']} {info['os_version']}".strip(),
            "xray": xray_result,
        }

    # xray 部分失败时，VPS 已入库但 xray_status 是失败态
    logger.warning(
        "VPS 已登记成功，但 xray 流程出问题了：ip=%s xray 状态=%s（可以单独跑 xrayinit 重试）",
        ip, xray_result["status"],
    )
    return {
        "status": "ok_xray_partial",
        "ip": ip,
        "os": f"{info['os_name']} {info['os_version']}".strip(),
        "xray": xray_result,
        "message": "VPS 已入库但 xray 安装/启动失败，可重跑 xrayinit 单独处理",
    }
