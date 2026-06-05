"""xray 配置层：构造 config dict 的纯函数 + 配置文件 SSH 操作。

本模块包含两类东西：

1. 纯函数（不接 SSH / DB）：
   - build_proxy_outbound  ← 上游代理凭据 → outbound dict 片段
   - generate_random_auth  ← 随机账密（给客户端 inbound 用）
   - build_vps_direct_config   ← 完整 config：VPS 自用直出（freedom outbound）
   - build_proxy_relay_config  ← 完整 config：客户端走上游代理（代理跳转）

2. SSH 操作（接 paramiko client）：
   - get_config_size / is_config_blank
   - write_default_config   ← 把 VPS 直出场景默认 config 写到服务器
   - upload_config          ← 上传任意 config dict 到 DEFAULT_CONFIG_PATH
   - validate_config        ← 跑 `xray test -c` 验证 config 语法

xray 服务层（install/start/stop/enable/disable/...）在 xray/atom.py。
"""

from __future__ import annotations

import json
import secrets
import string

import paramiko

import config
from core.ssh import execute_command


# ============================================================
# 协议常量（外部业务可直接 import 使用）
# ============================================================

PROTOCOL_SOCKS5 = "socks5"
PROTOCOL_HTTP = "http"

# 业务层入参校验时用这个集合；扩展协议在此注册
SUPPORTED_PROTOCOLS = (PROTOCOL_SOCKS5, PROTOCOL_HTTP)

# xray config.json 里的 protocol 字段跟我们用户层略不同：
# 对外讲 "socks5"，xray 里写 "socks"。集中映射避免业务层硬编码。
_XRAY_PROTOCOL_NAME = {
    PROTOCOL_SOCKS5: "socks",
    PROTOCOL_HTTP: "http",
}


# ============================================================
# 路径 / 端口常量
# ============================================================

DEFAULT_CONFIG_PATH = "/usr/local/etc/xray/config.json"
DEFAULT_PORT = config.XRAY_DEFAULT_PORT


# ============================================================
# 错误文案
# ============================================================

UNSUPPORTED_PROTOCOL_MESSAGE = (
    "不支持的代理协议。当前仅支持 socks5 / http；"
    "如需扩展，请在 xray.config.SUPPORTED_PROTOCOLS 注册新值，"
    "并在 _XRAY_PROTOCOL_NAME 补充映射。"
)

PORT_CONFLICTS_WITH_DEFAULT_MESSAGE = (
    "客户端入站端口与 xray 默认直出端口冲突。"
    "rgIP 业务的端口应从 PROXY_PORT_RANGE_START..END 范围（默认 18441-18450）挑选；"
    "默认端口 18440 给 VPS 自用直出（default-direct），不能被客户端 inbound 占用。"
)

CONFIG_WRITE_FAILED_MESSAGE = (
    "向 xray config 文件写入内容失败。"
    "常见原因：用户无写权限 / 目录不存在 / 磁盘满。"
    "建议：登录服务器跑 `ls -l /usr/local/etc/xray/` 看权限和挂载状态。"
)

CONFIG_VALIDATION_FAILED_MESSAGE = (
    "xray test -c config.json 校验失败：配置语法或语义错误。"
    "建议：登录服务器跑 `xray test -confdir /usr/local/etc/xray/` 看具体行号；"
    "或先回滚到上一个已知能用的 config 再排查。"
)


# ============================================================
# 错误类
# ============================================================

class UnsupportedProtocolError(ValueError):
    """传入了 SUPPORTED_PROTOCOLS 之外的协议字符串。"""


class PortConflictError(ValueError):
    """客户端入站端口跟 xray 默认直出端口（DEFAULT_PORT）撞了。"""


class ConfigWriteError(RuntimeError):
    """写 config 文件到服务器失败（权限 / 目录 / 磁盘等）。"""


class ConfigValidationError(RuntimeError):
    """xray test -c 校验 config 失败。"""


# ============================================================
# 内部常量：默认直出场景的 inbound / outbound dict
# ----- 这俩是 build_vps_direct_config 和 build_proxy_relay_config 共用的"砖" -----
# ============================================================

# VPS 自用直出 inbound：监听 DEFAULT_PORT 的免认证 socks5
_DEFAULT_DIRECT_INBOUND = {
    "tag": "default-direct",
    "port": DEFAULT_PORT,
    "listen": "0.0.0.0",
    "protocol": "socks",
    "settings": {"auth": "noauth", "udp": True},
}

# VPS 自用直出 outbound：freedom 直连，不走任何代理
_DEFAULT_DIRECT_OUTBOUND = {
    "tag": "direct",
    "protocol": "freedom",
}


# ============================================================
# 纯函数：随机账密生成（给客户端 inbound 用）
# ============================================================

_AUTH_ALPHABET = string.ascii_letters + string.digits
_DEFAULT_AUTH_LEN = 16


def generate_random_auth(length: int = _DEFAULT_AUTH_LEN) -> tuple[str, str]:
    """生成随机 (user, pwd) 给客户端 inbound 账密用。

    用 secrets 模块（加密安全），字符集是大小写字母 + 数字（避免 shell / URL 转义麻烦）。
    默认长度 16，可调；同一次调用 user 和 pwd 是两条独立的随机串。

    返回 (user, pwd) 元组，业务层拿去：
        ① 入库（落到代理节点记录的 inbound_user / inbound_pwd_encrypted）
        ② 传给 build_proxy_relay_config(inbound_user=u, inbound_pwd=p)
        ③ 返回给用户作为客户端连接凭据
    """
    user = "".join(secrets.choice(_AUTH_ALPHABET) for _ in range(length))
    pwd = "".join(secrets.choice(_AUTH_ALPHABET) for _ in range(length))
    return user, pwd


# ============================================================
# 纯函数：构造 config dict 片段（outbound 原语）
# ============================================================

def build_proxy_outbound(
    host: str,
    port: int,
    user: str,
    pwd: str,
    protocol: str,
    tag: str = "proxy-out",
) -> dict:
    """把一条上游代理凭据翻成 xray outbound 字典片段。

    用法：把返回的 dict 加进完整 xray config 的 outbounds[] 里，
    再用 routing.rules 把对应 inbound 路由到这个 tag。

    参数：
        host / port: 上游代理入口（entry_host / entry_port）
        user / pwd : 上游代理账密（同时为空 = 免认证代理；任一非空则按账密认证装）
        protocol  : "socks5" 或 "http"（用 PROTOCOL_* 常量传更安全）
        tag       : outbound 在 xray 里的标识，路由规则用它指代本 outbound

    传入未注册的 protocol 抛 UnsupportedProtocolError。
    """
    if protocol not in SUPPORTED_PROTOCOLS:
        raise UnsupportedProtocolError(
            f"{UNSUPPORTED_PROTOCOL_MESSAGE} 传入的是: {protocol!r}"
        )

    server: dict = {"address": host, "port": port}
    # user 或 pwd 任一非空就带 users（有些代理只校验 user，pwd 可空）
    if user or pwd:
        server["users"] = [{"user": user, "pass": pwd}]

    return {
        "tag": tag,
        "protocol": _XRAY_PROTOCOL_NAME[protocol],
        "settings": {"servers": [server]},
    }


# ============================================================
# 纯函数：场景级完整 config 构造（顶层入口）
# ============================================================

def build_vps_direct_config() -> dict:
    """场景一：VPS 自用直出。

    inbounds:  [default-direct (18440 socks noauth)]
    outbounds: [direct (freedom)]
    routing:   default-direct → direct

    业务用途：装完 xray 默认写这个 config 让服务能起来（rgvps / install_xray 用）。
    """
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_DEFAULT_DIRECT_INBOUND],
        "outbounds": [_DEFAULT_DIRECT_OUTBOUND],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["default-direct"],
                    "outboundTag": "direct",
                },
            ],
        },
    }


def build_proxy_relay_config(
    vps_port: int,
    proxy_outbound: dict,
    inbound_user: str,
    inbound_pwd: str,
) -> dict:
    """场景二：客户端走上游代理跳转。

    inbounds:
        ① default-direct       ← DEFAULT_PORT 上的免认证 socks5（保留 VPS 自用通路）
        ② client-{vps_port}    ← vps_port 上的账密 socks5（客户端连这个）
    outbounds:
        ① direct               ← freedom 直出（VPS 自用）
        ② <proxy_outbound>     ← 上游代理（caller 用 build_proxy_outbound 造好传入）
    routing:
        ① default-direct → direct
        ② client-{vps_port} → <proxy_outbound 的 tag>

    参数：
        vps_port       : 客户端要连的端口（必须 != DEFAULT_PORT）
        proxy_outbound : build_proxy_outbound 产出的 outbound dict
        inbound_user   : 客户端连本机的账号（业务层用 generate_random_auth() 生成）
        inbound_pwd    : 客户端连本机的密码（同上）

    失败：vps_port == DEFAULT_PORT → 抛 PortConflictError
    """
    if vps_port == DEFAULT_PORT:
        raise PortConflictError(
            f"{PORT_CONFLICTS_WITH_DEFAULT_MESSAGE} 传入 vps_port={vps_port}"
        )

    client_tag = f"client-{vps_port}"

    client_inbound = {
        "tag": client_tag,
        "port": vps_port,
        "listen": "0.0.0.0",
        "protocol": "socks",
        "settings": {
            "auth": "password",
            "udp": True,
            "accounts": [{"user": inbound_user, "pass": inbound_pwd}],
        },
    }

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [_DEFAULT_DIRECT_INBOUND, client_inbound],
        "outbounds": [_DEFAULT_DIRECT_OUTBOUND, proxy_outbound],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["default-direct"],
                    "outboundTag": "direct",
                },
                {
                    "type": "field",
                    "inboundTag": [client_tag],
                    "outboundTag": proxy_outbound["tag"],
                },
            ],
        },
    }


# ============================================================
# 兼容常量：默认 config 的 JSON 字符串形式
# （从 build_vps_direct_config 实时算，确保跟 dict 同源；外部代码若已依赖此名仍可用）
# ============================================================

DEFAULT_CONFIG_JSON = json.dumps(build_vps_direct_config(), indent=2)


# ============================================================
# SSH 操作：查 config 文件大小 / 是否空
# ============================================================

def get_config_size(client: paramiko.SSHClient) -> int:
    """获取 config.json 文件大小（字节）。不存在或读取失败返回 0。"""
    result = execute_command(
        client,
        f"stat -c %s {DEFAULT_CONFIG_PATH} 2>/dev/null || echo 0",
    )
    try:
        return int(result["stdout"].strip())
    except (ValueError, KeyError):
        return 0


def is_config_blank(client: paramiko.SSHClient) -> bool:
    """检查 config.json 是否空（缺失或 0 字节）。"""
    return get_config_size(client) == 0


# ============================================================
# SSH 操作：写 config 文件
# ============================================================

def write_default_config(client: paramiko.SSHClient) -> None:
    """把 VPS 直出场景的默认 config 写到服务器。

    用途：xray 装完但 config 空（x-ui 类面板场景）→ 服务起不来。
    写默认 config 让服务能正常 active，业务后续往里加节点。

    内部调用 upload_config(build_vps_direct_config())，失败抛 ConfigWriteError。
    """
    upload_config(client, build_vps_direct_config())


def upload_config(client: paramiko.SSHClient, config_dict: dict) -> None:
    """把任意 config dict 序列化后写到 DEFAULT_CONFIG_PATH。

    用 heredoc 写入避免 JSON 中的 " 被 shell 解析。
    写入失败抛 ConfigWriteError，文案带 stderr 便于排查。
    """
    payload = json.dumps(config_dict, indent=2)
    cmd = (
        f"cat > {DEFAULT_CONFIG_PATH} << 'XRAY_CONFIG_EOF'\n"
        f"{payload}\n"
        f"XRAY_CONFIG_EOF"
    )
    result = execute_command(client, cmd)
    if result["exit_code"] != 0:
        raise ConfigWriteError(
            f"{CONFIG_WRITE_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


# ============================================================
# SSH 操作：校验 config 语法
# ============================================================

def validate_config(client: paramiko.SSHClient) -> None:
    """跑 `xray test -confdir /usr/local/etc/xray` 校验 config 语法。

    校验失败抛 ConfigValidationError，文案带 xray 的 stderr 便于定位。
    业务一般在 upload_config 后、reload 前调一次，避免推坏 config 上线。
    """
    # 用 confdir 而不是 -c <file>，让 xray 自己 glob 配置目录（更贴近 systemd 启动行为）
    result = execute_command(client, "xray test -confdir /usr/local/etc/xray")
    if result["exit_code"] != 0:
        # xray 把校验错误打在 stdout 或 stderr，取两者拼起来
        detail = (result["stderr"] or result["stdout"])[:300]
        raise ConfigValidationError(
            f"{CONFIG_VALIDATION_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"detail={detail}"
        )
