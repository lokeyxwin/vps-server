"""xray 原子层：操作 xray 二进制 + systemd 的低层函数 + 异常类。

所有函数接收 paramiko.SSHClient，不感知 DB / 不感知业务。
"""

from __future__ import annotations

import paramiko

import config
from core.ssh import execute_command


# ============================================================
# 命令模板
# ============================================================

INSTALL_COMMAND = (
    'bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install'
)
UNINSTALL_COMMAND = (
    'bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ remove --purge'
)

# 装机超时（GitHub 拉取，国内可能慢）。从 config 取，方便运维统一调
INSTALL_TIMEOUT = config.XRAY_INSTALL_TIMEOUT


# ============================================================
# 默认 config（VPS 自身代理端口 + freedom 直出）
# ============================================================
#
# 端口分配约定：
#   18440           ← xray 默认核心配置端口（VPS 自身的 socks 直出，配置于 config.XRAY_DEFAULT_PORT）
#   18441-18450     ← Proxy 业务部署的代理出口（每个 VPS 最多 10 个）
#
# 为啥要写默认 config：
#   xray-install 装完后 config.json 可能是空的（x-ui 面板类场景下需要后续 panel 添加节点）。
#   没 config 就 `systemctl start xray` 立刻退出 code=23（systemd 不会重启）。
#   写一个最小可用 config 让服务能正常 active，业务后续往里加 inbounds 即可。
#
DEFAULT_CONFIG_PATH = "/usr/local/etc/xray/config.json"
DEFAULT_PORT = config.XRAY_DEFAULT_PORT
DEFAULT_CONFIG_JSON = f'''{{
  "log": {{"loglevel": "warning"}},
  "inbounds": [
    {{
      "tag": "default-direct",
      "port": {DEFAULT_PORT},
      "listen": "0.0.0.0",
      "protocol": "socks",
      "settings": {{"auth": "noauth", "udp": true}}
    }}
  ],
  "outbounds": [
    {{"protocol": "freedom", "tag": "direct"}}
  ]
}}'''


# ============================================================
# 错误文案常量
# ============================================================

XRAY_INSTALL_FAILED_MESSAGE = (
    "xray 安装失败。"
    "常见原因："
    "① 无法访问 GitHub（curl 拉脚本失败）；"
    "② 系统包管理器异常；"
    "③ 磁盘满。"
    "建议：登录服务器手动跑 "
    "`bash -c \"$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)\" @ install` "
    "看具体错误。"
)
XRAY_UNINSTALL_FAILED_MESSAGE = "xray 卸载失败。建议：登录服务器手动跑卸载脚本看具体错误。"
XRAY_VERIFY_FAILED_MESSAGE = (
    "xray 二进制存在但 `xray --version` 无输出，疑似二进制损坏。"
    "建议：登录服务器跑 `xray --version 2>&1` 看实际错误；"
    "如确实损坏，请重装或卸载后重装（uninstall + install）。"
)
XRAY_SERVICE_START_FAILED_MESSAGE = (
    "systemctl start xray 命令失败。"
    "建议：登录服务器跑 `systemctl start xray && systemctl status xray --no-pager -l` 看错误。"
)
XRAY_SERVICE_NOT_ACTIVE_MESSAGE = (
    "已尝试 systemctl start xray 但服务仍未 active。"
    "常见原因："
    "① config.json 语法错误；"
    "② 监听端口被占用；"
    "③ 文件权限或 SELinux/AppArmor 拦截。"
    "建议：登录服务器跑 `systemctl status xray --no-pager -l` + `journalctl -u xray -n 50` 看启动错误。"
)
XRAY_ENABLE_FAILED_MESSAGE = (
    "systemctl enable xray 失败（无法设置开机自启）。"
    "常见原因："
    "① /etc/systemd/system/xray.service 文件缺失或损坏；"
    "② systemd 配置异常。"
    "建议：登录服务器跑 `systemctl enable xray` 看具体错误；服务本身可能仍在跑，重启服务器后会丢失。"
)


# ============================================================
# 错误类（统一基类 XrayError，业务可分类捕获，每个子类有 code）
# ============================================================

class XrayError(Exception):
    """xray 操作的统一基类。"""
    code: str = "xray_error"


class InstallFailedError(XrayError):
    code = "install_failed"


class UninstallFailedError(XrayError):
    code = "uninstall_failed"


class VerifyFailedError(XrayError):
    code = "verify_failed"


class ServiceNotActiveError(XrayError):
    code = "service_not_active"


class EnableFailedError(XrayError):
    code = "enable_failed"


# ============================================================
# 原子函数：查询类
# ============================================================

def is_installed(client: paramiko.SSHClient) -> bool:
    """检查 xray 二进制是否存在。"""
    result = execute_command(client, "command -v xray")
    return result["exit_code"] == 0 and result["stdout"].strip() != ""


def is_service_active(client: paramiko.SSHClient) -> bool:
    """检查 xray systemd 服务是否处于 active 状态。"""
    result = execute_command(client, "systemctl is-active xray 2>/dev/null")
    return result["stdout"].strip() == "active"


def is_service_enabled(client: paramiko.SSHClient) -> bool:
    """检查 xray systemd 服务是否开机自启。"""
    result = execute_command(client, "systemctl is-enabled xray 2>/dev/null")
    return result["stdout"].strip() == "enabled"


def get_version(client: paramiko.SSHClient) -> str:
    """获取 xray 版本号。未安装或失败返回 ''。"""
    result = execute_command(client, "xray version 2>/dev/null | head -n1")
    if result["exit_code"] != 0:
        return ""
    return result["stdout"].strip()


# ============================================================
# 原子函数：操作类
# ============================================================

def install(client: paramiko.SSHClient) -> None:
    """安装 xray。失败抛 InstallFailedError。"""
    result = execute_command(client, INSTALL_COMMAND, timeout=INSTALL_TIMEOUT)
    if result["exit_code"] != 0:
        raise InstallFailedError(
            f"{XRAY_INSTALL_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


def uninstall(client: paramiko.SSHClient) -> None:
    """卸载 xray。失败抛 UninstallFailedError。"""
    result = execute_command(client, UNINSTALL_COMMAND, timeout=INSTALL_TIMEOUT)
    if result["exit_code"] != 0:
        raise UninstallFailedError(
            f"{XRAY_UNINSTALL_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


def start(client: paramiko.SSHClient) -> None:
    """启动 xray systemd 服务。失败抛 ServiceNotActiveError。"""
    result = execute_command(client, "systemctl start xray")
    if result["exit_code"] != 0:
        raise ServiceNotActiveError(
            f"{XRAY_SERVICE_START_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


def enable(client: paramiko.SSHClient) -> None:
    """设置 xray 开机自启。失败抛 EnableFailedError。"""
    result = execute_command(client, "systemctl enable xray")
    if result["exit_code"] != 0:
        raise EnableFailedError(
            f"{XRAY_ENABLE_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


# ============================================================
# 配置文件操作（用于"config 空 → 写默认让服务能跑"场景）
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


def test_internal_socks(
    client: paramiko.SSHClient,
    port: int = DEFAULT_PORT,
    test_url: str = config.CONNECTIVITY_TEST_URL,
    timeout: int = config.CONNECTIVITY_TEST_TIMEOUT,
) -> dict:
    """在服务器内部测试 xray socks5 是否真的能转发请求。

    在服务器上跑 curl --socks5 127.0.0.1:PORT 验证 xray 自身正常工作。
    跟外部 ping 互补：内部通＝服务器/xray/config 都正常；
    外部不通＝防火墙或云服务商安全组拦截。

    返回 {"ok": bool, "http_code": int|None, "body": str, "error": str|None}
    """
    # 一行 curl，输出 "HTTP_CODE|BODY" 方便解析
    cmd = (
        f"curl --socks5 127.0.0.1:{port} -m {timeout} -s "
        f"-w '__HTTPCODE__%{{http_code}}' -o /tmp/_xray_internal_test.out "
        f"{test_url} 2>&1 ; "
        f"echo '__BODY__' ; cat /tmp/_xray_internal_test.out 2>/dev/null ; "
        f"rm -f /tmp/_xray_internal_test.out"
    )
    result = execute_command(client, cmd, timeout=timeout + 5)
    out = result["stdout"]

    # 解析 http_code
    http_code = None
    if "__HTTPCODE__" in out:
        try:
            code_part = out.split("__HTTPCODE__", 1)[1].split("__BODY__", 1)[0]
            http_code = int(code_part.strip())
        except (ValueError, IndexError):
            http_code = None

    # 解析 body
    body = ""
    if "__BODY__" in out:
        body = out.split("__BODY__", 1)[1].strip()[:200]

    ok = http_code == 200
    return {
        "ok": ok,
        "http_code": http_code,
        "body": body,
        "error": None if ok else f"http_code={http_code} body={body!r}",
    }


def write_default_config(client: paramiko.SSHClient) -> None:
    """写入最小可用默认 config：监听 18440 socks，freedom 直出。

    仅应在 config 空/缺失时调用，**不要覆盖用户已有内容**。
    业务场景：装了 xray 但 config.json 是空的（如 x-ui 类面板未添加节点），
    服务起不来。写默认 config 后能让服务正常 active，业务后续往里加节点。
    """
    # 用 heredoc 避免 JSON 里的 " 被 shell 解析
    cmd = (
        f"cat > {DEFAULT_CONFIG_PATH} << 'XRAY_DEFAULT_CONFIG_EOF'\n"
        f"{DEFAULT_CONFIG_JSON}\n"
        f"XRAY_DEFAULT_CONFIG_EOF"
    )
    result = execute_command(client, cmd)
    if result["exit_code"] != 0:
        raise InstallFailedError(
            f"写入默认 xray config 失败: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )
