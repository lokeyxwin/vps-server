"""xray 原子层：操作 xray 二进制 + systemd 的低层函数 + 异常类。

所有函数接收 paramiko.SSHClient，不感知 DB / 不感知业务。
"""

from __future__ import annotations

import paramiko

import config
from ssh.ops import execute_command


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


# 默认 config 相关常量 + 构造 / 上传 / 校验函数已搬到 xray/config.py。
# 这里只保留与服务运行时（install / start / stop / 自检）相关的内容。


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
XRAY_SERVICE_STOP_FAILED_MESSAGE = (
    "systemctl stop xray 命令失败。"
    "常见原因：用户无 root 权限 / xray 服务单元损坏。"
    "建议：登录服务器跑 `systemctl stop xray && systemctl status xray --no-pager -l` 看错误。"
)
XRAY_DISABLE_FAILED_MESSAGE = (
    "systemctl disable xray 失败（无法关闭开机自启）。"
    "常见原因：用户无 root 权限 / 服务单元已损坏。"
    "建议：登录服务器跑 `systemctl disable xray` 看具体错误。"
)
XRAY_RELOAD_FAILED_MESSAGE = (
    "systemctl reload xray 失败（无法让 xray 重新加载配置）。"
    "常见原因："
    "① config.json 语法错误（新配置 reload 不进去）；"
    "② 服务当前未运行（reload 一个 inactive 服务会失败）；"
    "③ 用户无 root 权限。"
    "建议：登录服务器跑 `xray test -confdir /usr/local/etc/xray/` 校验配置语法；"
    "再跑 `systemctl status xray --no-pager -l` 看状态。"
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


class StopFailedError(XrayError):
    code = "stop_failed"


class DisableFailedError(XrayError):
    code = "disable_failed"


class ReloadFailedError(XrayError):
    code = "reload_failed"


# ============================================================
# 原子函数：查询类
# ============================================================

def is_installed(client: paramiko.SSHClient) -> bool:
    """检查 xray 二进制是否存在。"""
    result = execute_command(client, "command -v xray")
    return result["exit_code"] == 0 and result["stdout"].strip() != ""


def is_running(client: paramiko.SSHClient) -> bool:
    """检查 xray systemd 服务是否处于 active（运行中）状态。"""
    result = execute_command(client, "systemctl is-active xray 2>/dev/null")
    return result["stdout"].strip() == "active"


def is_enabled(client: paramiko.SSHClient) -> bool:
    """检查 xray systemd 服务是否开机自启。"""
    result = execute_command(client, "systemctl is-enabled xray 2>/dev/null")
    return result["stdout"].strip() == "enabled"


def version(client: paramiko.SSHClient) -> str:
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


def stop(client: paramiko.SSHClient) -> None:
    """停止 xray systemd 服务。失败抛 StopFailedError。"""
    result = execute_command(client, "systemctl stop xray")
    if result["exit_code"] != 0:
        raise StopFailedError(
            f"{XRAY_SERVICE_STOP_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


def disable(client: paramiko.SSHClient) -> None:
    """关闭 xray 开机自启。失败抛 DisableFailedError。"""
    result = execute_command(client, "systemctl disable xray")
    if result["exit_code"] != 0:
        raise DisableFailedError(
            f"{XRAY_DISABLE_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stderr={result['stderr'][:200]}"
        )


def reload(client: paramiko.SSHClient) -> None:
    """让 xray 重新加载 config。

    优先 `systemctl reload xray`（无打断，需要 unit 配 ExecReload=SIGHUP）；
    某些 xray 装机脚本（如 666clouds 镜像）没配 ExecReload，reload 会报
    "Job type reload is not applicable"——这种情况自动 fallback 到
    `systemctl restart xray`（有短暂连接打断但通用）。

    业务层应该在 reload 前先 validate_config，避免推坏 config 上线。
    """
    # `|| restart` 兜底：reload 失败时返回非零 exit_code，
    # `||` 触发 restart；restart 成功后整条命令 exit=0
    cmd = "systemctl reload xray 2>&1 || systemctl restart xray"
    result = execute_command(client, cmd)
    if result["exit_code"] != 0:
        raise ReloadFailedError(
            f"{XRAY_RELOAD_FAILED_MESSAGE}: exit={result['exit_code']} "
            f"stdout={result['stdout'][:120]} stderr={result['stderr'][:120]}"
        )


# ============================================================
# 服务自检：在 VPS 内部 curl 走 xray socks 测试
# （留在 service 层而不是 config 层：测试的是「运行时服务」而非「配置文件」）
# ============================================================

def test_internal_socks(
    client: paramiko.SSHClient,
    port: int = config.XRAY_DEFAULT_PORT,
    test_url: str = config.CONNECTIVITY_TEST_URL,
    timeout: int = config.CONNECTIVITY_TEST_TIMEOUT,
    user: str = "",
    pwd: str = "",
) -> dict:
    """在服务器内部测试 xray socks5 是否真的能转发请求。

    在服务器上跑 curl --socks5 127.0.0.1:PORT 验证 xray 自身正常工作。
    跟外部 ping 互补：内部通＝服务器/xray/config 都正常；
    外部不通＝防火墙或云服务商安全组拦截。

    user/pwd：
    - 都空 → 走 noauth 模式（用于测 default-direct 这种无账密 inbound）
    - 非空 → curl 用 socks5h://user:pwd@host:port 形式带账密
            （用于测 rgIP 部署的账密 inbound）

    返回 {"ok": bool, "http_code": int|None, "body": str, "error": str|None}
    """
    # 选 socks5 URL 形式：带 auth 走 socks5h URL，无 auth 走 --socks5 host:port
    if user or pwd:
        # 用 -x 而不是 --socks5（后者不接受 user:pwd@... 语法）
        proxy_arg = f"-x 'socks5h://{user}:{pwd}@127.0.0.1:{port}'"
    else:
        proxy_arg = f"--socks5 127.0.0.1:{port}"
    # 一行 curl，输出 "HTTP_CODE|BODY" 方便解析
    cmd = (
        f"curl {proxy_arg} -m {timeout} -s "
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
