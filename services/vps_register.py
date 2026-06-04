"""注册 VPS 业务：一站式 provisioning。

流程：
    ① 查重（DB 已有 IP → duplicate）
    ② SSH 测连 + 采集系统信息（4 种错误细分：auth/timeout/refused/failed）
    ③ 入库（密码加密、xray_status='not_installed' 默认）
    ④ 调用 install_xray_on_vps 业务把 xray 也搞定（同一函数复用）
    ⑤ 返回最终 status

入库后 xray 全流程也会跑：装 → 起 → 自启。
"""

from datetime import date

from db import VPSRecord, session_scope
from log import get_logger
from services.vps_install_xray import install_xray_on_vps
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
) -> dict:
    """注册一台 VPS + 顺手装好 xray + 启用服务。

    返回 status 枚举：
        ok             - 成功（注册 + xray 全流程）
        ok_xray_partial - 注册成功但 xray 部分失败（看 xray_status 字段）
        duplicate      - 该 IP 已在表中
        auth_failed / timeout / refused / failed - SSH 连接失败
    """
    logger.info("register_vps 开始 ip=%s user=%s port=%s", ip, username, port)

    # ① 查重
    with session_scope() as session:
        if session.query(VPSRecord).filter_by(ip=ip).first() is not None:
            logger.info("register_vps 跳过 ip=%s 原因=已存在", ip)
            return {"status": "duplicate", "message": f"IP {ip} 已存在数据库"}

    # ② SSH 测连 + 采集系统信息
    try:
        with VPSSession(ip, username, password, port) as vps:
            info = vps.get_system_info()
    except AuthFailedError as exc:
        logger.warning("register_vps 失败 ip=%s status=auth_failed", ip)
        return {"status": "auth_failed", "message": str(exc)}
    except ConnectTimeoutError as exc:
        logger.warning("register_vps 失败 ip=%s status=timeout", ip)
        return {"status": "timeout", "message": str(exc)}
    except ConnectRefusedError as exc:
        logger.warning("register_vps 失败 ip=%s status=refused", ip)
        return {"status": "refused", "message": str(exc)}
    except ConnectionError as exc:
        logger.warning("register_vps 失败 ip=%s status=failed", ip)
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
        )
        session.add(record)

    logger.info(
        "register_vps 基础入库完成 ip=%s os=%s %s，下一步装 xray",
        ip, info["os_name"], info["os_version"],
    )

    # ④ 链式调用 install_xray_on_vps —— 复用业务，避免逻辑重复
    xray_result = install_xray_on_vps(ip)

    # ⑤ 合成最终返回
    if xray_result["status"] in ("ok", "imported", "already_running"):
        logger.info(
            "register_vps 全流程成功 ip=%s os=%s %s xray=%s",
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
        "register_vps 注册成功但 xray 流程异常 ip=%s xray_status=%s",
        ip, xray_result["status"],
    )
    return {
        "status": "ok_xray_partial",
        "ip": ip,
        "os": f"{info['os_name']} {info['os_version']}".strip(),
        "xray": xray_result,
        "message": "VPS 已入库但 xray 安装/启动失败，可重跑 xrayinit 单独处理",
    }
