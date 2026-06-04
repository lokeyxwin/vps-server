"""SSH 通用原子层：连接 / 执行 / 文件传输 / 系统信息采集。

所有领域（vps 业务 / xray / ip / proxy ...）都通过这里跟服务器对话。
"""

import socket

import paramiko

from log import get_logger


logger = get_logger(__name__)


# ============================================================
# 错误文案常量
# ============================================================

CONNECTION_ERROR_MESSAGE = "服务器连接失败请检查IP账号密码端口是否正确"
AUTH_FAILED_MESSAGE = (
    "认证失败：用户名或密码错误。"
    "常见原因："
    "① 密码源给错了——服务商面板/控制台密码 ≠ 服务器 SSH 密码，请确认是『服务器登录密码』；"
    "② OCR 易混淆字符 o/0、I/l/1，请重点核对密码；"
    "③ 服务器开启了密钥登录、禁用了密码登录（检查 /etc/ssh/sshd_config 的 PasswordAuthentication）。"
)
CONNECT_TIMEOUT_MESSAGE = (
    "连接超时：SYN 包发出后无应答，包没到服务器或被默默丢弃。"
    "常见原因："
    "① 云服务商安全组/防火墙未放行端口（去控制台查安全规则）；"
    "② 服务器本地防火墙 DROP 包（登录后跑 firewall-cmd --list-ports 或 ufw status）；"
    "③ IP 写错 / 服务器宕机 / 网络不通。"
)
CONNECT_REFUSED_MESSAGE = (
    "连接被拒绝：包到达了服务器但没人监听该端口。"
    "常见原因："
    "① SSH 服务未运行（登录后 systemctl status sshd）；"
    "② 端口号写错（默认 22，自定义端口请核对，OCR 可能把数字看错）；"
    "③ 本地防火墙策略为 REJECT 而非 DROP。"
)

EXECUTE_ERROR_MESSAGE = "命令执行失败请检查连接状态或命令是否正确"
FILE_TRANSFER_ERROR_MESSAGE = "文件传输失败请检查路径或权限"


# ============================================================
# 错误类
# ============================================================

class AuthFailedError(ConnectionError):
    """SSH 认证失败：用户名/密码错误。"""


class ConnectTimeoutError(ConnectionError):
    """TCP 连接超时：端口未开放或网络不通。"""


class ConnectRefusedError(ConnectionError):
    """连接被拒绝：端口未在监听 SSH 服务。"""


# ============================================================
# 原子函数
# ============================================================

def connect_server(
    ip: str, username: str, password: str, port: int
) -> paramiko.SSHClient:
    """建立 SSH 连接，返回 SSHClient。

    失败时抛 ConnectionError 子类（AuthFailedError / ConnectTimeoutError /
    ConnectRefusedError），其他未分类异常抛 ConnectionError。
    """
    logger.info("尝试连接 %s@%s:%s", username, ip, port)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            port=port,
            username=username,
            password=password,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        logger.info("连接成功 %s@%s:%s", username, ip, port)
        return client
    except paramiko.AuthenticationException as exc:
        client.close()
        logger.warning("认证失败 ip=%s user=%s reason=%s", ip, username, exc)
        raise AuthFailedError(AUTH_FAILED_MESSAGE) from exc
    except (socket.timeout, TimeoutError) as exc:
        client.close()
        logger.warning("连接超时 ip=%s port=%s reason=%s", ip, port, exc)
        raise ConnectTimeoutError(CONNECT_TIMEOUT_MESSAGE) from exc
    except ConnectionRefusedError as exc:
        client.close()
        logger.warning("连接被拒 ip=%s port=%s reason=%s", ip, port, exc)
        raise ConnectRefusedError(CONNECT_REFUSED_MESSAGE) from exc
    except Exception as exc:
        client.close()
        logger.error("连接失败（未分类） ip=%s port=%s type=%s reason=%s",
                     ip, port, type(exc).__name__, exc)
        raise ConnectionError(CONNECTION_ERROR_MESSAGE) from exc


def close_server(client: paramiko.SSHClient) -> None:
    """关闭 SSH 连接。重复调用安全，传入 None 也安全。"""
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def execute_command(
    client: paramiko.SSHClient, command: str, timeout: int = 30
) -> dict:
    """执行远程命令。

    timeout 是 channel 读超时（不是命令最长时间，只要有持续输出就不会触发）。
    长任务（如装软件）可以传更大值，比如 120。

    返回 {"stdout": str, "stderr": str, "exit_code": int}。
    执行通道异常时抛 RuntimeError。
    """
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()
        return {"stdout": out, "stderr": err, "exit_code": exit_code}
    except Exception as exc:
        raise RuntimeError(EXECUTE_ERROR_MESSAGE) from exc


def upload_file(client: paramiko.SSHClient, local_path: str, remote_path: str) -> None:
    """通过 SFTP 上传文件。失败统一抛 RuntimeError。"""
    try:
        with client.open_sftp() as sftp:
            sftp.put(local_path, remote_path)
    except Exception as exc:
        raise RuntimeError(FILE_TRANSFER_ERROR_MESSAGE) from exc


def download_file(client: paramiko.SSHClient, remote_path: str, local_path: str) -> None:
    """通过 SFTP 下载文件。失败统一抛 RuntimeError。"""
    try:
        with client.open_sftp() as sftp:
            sftp.get(remote_path, local_path)
    except Exception as exc:
        raise RuntimeError(FILE_TRANSFER_ERROR_MESSAGE) from exc


def _parse_os_release(text: str) -> dict:
    parsed = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def get_system_info(client: paramiko.SSHClient) -> dict:
    """采集系统基础信息。任一字段失败时为空字符串，不抛异常。"""
    info = {"username": "", "os_name": "", "os_version": ""}

    who = execute_command(client, "whoami")
    if who["exit_code"] == 0:
        info["username"] = who["stdout"].strip()

    os_release = execute_command(client, "cat /etc/os-release")
    if os_release["exit_code"] == 0:
        parsed = _parse_os_release(os_release["stdout"])
        info["os_name"] = parsed.get("NAME", "")
        info["os_version"] = parsed.get("VERSION_ID") or parsed.get("VERSION", "")

    return info
