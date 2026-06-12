"""非 root VPS 的 sudo 适配：特权命令前置 `sudo -n`，只读命令不加。

只测 mock 层（不连真服务器）：
- sudo_prefix / service.is_root
- 特权服务原子（install/start/...）按 use_sudo 决定是否前缀
- upload_config 用 `sudo -n tee` 落盘（不能用 `sudo cat > file`，重定向由当前 shell 执行）
- XrayManager.use_sudo 惰性探一次 `id -u` 并缓存，透传给特权方法
"""

from unittest.mock import MagicMock, patch

from ssh.ops import sudo_prefix
from xray import config as xc
from xray import service
from xray.manager import XrayManager


def _ok(stdout: str = "", exit_code: int = 0) -> dict:
    return {"stdout": stdout, "stderr": "", "exit_code": exit_code}


# ============ sudo_prefix ============

def test_sudo_prefix_non_root_returns_prefix():
    assert sudo_prefix(True) == "sudo -n "


def test_sudo_prefix_root_returns_empty():
    assert sudo_prefix(False) == ""


# ============ service.is_root ============

def test_is_root_true_when_uid_zero():
    with patch.object(service, "execute_command", return_value=_ok("0")):
        assert service.is_root(MagicMock()) is True


def test_is_root_false_when_uid_nonzero():
    with patch.object(service, "execute_command", return_value=_ok("1000")):
        assert service.is_root(MagicMock()) is False


# ============ 特权服务原子按 use_sudo 前缀 ============

def test_start_prefixes_sudo_when_non_root():
    with patch.object(service, "execute_command", return_value=_ok()) as ex:
        service.start(MagicMock(), use_sudo=True)
    assert ex.call_args.args[1] == "sudo -n systemctl start xray"


def test_start_no_sudo_when_root():
    with patch.object(service, "execute_command", return_value=_ok()) as ex:
        service.start(MagicMock(), use_sudo=False)
    assert ex.call_args.args[1] == "systemctl start xray"


def test_install_prefixes_sudo_when_non_root():
    with patch.object(service, "execute_command", return_value=_ok()) as ex:
        service.install(MagicMock(), use_sudo=True)
    assert ex.call_args.args[1].startswith("sudo -n bash -c")


def test_reload_prefixes_sudo_on_both_branches():
    with patch.object(service, "execute_command", return_value=_ok()) as ex:
        service.reload(MagicMock(), use_sudo=True)
    cmd = ex.call_args.args[1]
    assert cmd == "sudo -n systemctl reload xray 2>&1 || sudo -n systemctl restart xray"


# ============ upload_config 用 sudo -n tee ============

def test_upload_config_uses_sudo_tee_when_non_root():
    with patch.object(xc, "execute_command", return_value=_ok()) as ex:
        xc.upload_config(MagicMock(), {"a": 1}, use_sudo=True)
    cmd = ex.call_args.args[1]
    assert cmd.startswith(f"sudo -n tee {xc.DEFAULT_CONFIG_PATH} > /dev/null << ")
    # 不能退化成 `sudo cat > file`（重定向会由非 root 当前 shell 执行而失败）
    assert "cat >" not in cmd


def test_upload_config_plain_tee_when_root():
    with patch.object(xc, "execute_command", return_value=_ok()) as ex:
        xc.upload_config(MagicMock(), {"a": 1}, use_sudo=False)
    cmd = ex.call_args.args[1]
    assert cmd.startswith(f"tee {xc.DEFAULT_CONFIG_PATH} > /dev/null << ")


# ============ XrayManager.use_sudo 惰性探测 + 缓存 + 透传 ============

def test_manager_use_sudo_true_for_non_root_and_cached():
    xm = XrayManager(MagicMock())
    with patch.object(service, "is_root", return_value=False) as is_root:
        assert xm.use_sudo is True
        assert xm.use_sudo is True  # 第二次走缓存
    is_root.assert_called_once()  # 只探一次 id -u


def test_manager_use_sudo_false_for_root():
    xm = XrayManager(MagicMock())
    with patch.object(service, "is_root", return_value=True):
        assert xm.use_sudo is False


def test_manager_install_threads_use_sudo_to_atom():
    xm = XrayManager(MagicMock())
    with patch.object(service, "is_root", return_value=False), \
            patch.object(service, "install") as install:
        xm.install()
    assert install.call_args.kwargs.get("use_sudo") is True
