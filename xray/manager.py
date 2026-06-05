"""XrayManager 类：封装一个 SSH 连接上的 xray 生命周期。

每个方法是 atom 的薄包装，外加一个高层 ensure_installed_and_running，
让业务层只调一个方法就把整个「装 + 启 + 自启」流程走完。
"""

from __future__ import annotations

import paramiko

from log import get_logger
from xray import atom
from xray import config as xc
from xray.atom import (
    InstallFailedError,
    UninstallFailedError,
    VerifyFailedError,
    ServiceNotActiveError,
    EnableFailedError,
    XRAY_VERIFY_FAILED_MESSAGE,
    XRAY_SERVICE_NOT_ACTIVE_MESSAGE,
)


logger = get_logger(__name__)


class XrayManager:
    """xray 在某一台 VPS 上的管理器。

    用法：
        with VPSManager.from_record(rec) as vps:
            xm = XrayManager(vps.client)
            result = xm.ensure_installed_and_running()
            # result = {"version": ..., "was_already_installed": bool, "actions_taken": [...]}
    """

    def __init__(self, client: paramiko.SSHClient) -> None:
        self.client = client

    # -------- 查询类（直接代理 atom）--------

    def is_installed(self) -> bool:
        return atom.is_installed(self.client)

    def is_running(self) -> bool:
        return atom.is_running(self.client)

    def is_enabled(self) -> bool:
        return atom.is_enabled(self.client)

    def version(self) -> str:
        return atom.version(self.client)

    # -------- 操作类（直接代理 atom，错误向上抛）--------

    def install(self) -> None:
        atom.install(self.client)

    def uninstall(self) -> None:
        atom.uninstall(self.client)

    def start(self) -> None:
        atom.start(self.client)

    def stop(self) -> None:
        atom.stop(self.client)

    def enable(self) -> None:
        atom.enable(self.client)

    def disable(self) -> None:
        atom.disable(self.client)

    # -------- 配置层（代理到 xray.config）--------

    def is_config_blank(self) -> bool:
        return xc.is_config_blank(self.client)

    def write_default_config(self) -> None:
        xc.write_default_config(self.client)

    def upload_config(self, config_dict: dict) -> None:
        xc.upload_config(self.client, config_dict)

    def validate_config(self) -> None:
        xc.validate_config(self.client)

    def test_internal_socks(self, port: int = xc.DEFAULT_PORT) -> dict:
        """在服务器内部测试 socks5 代理（默认 18440）。返回结果字典。"""
        return atom.test_internal_socks(self.client, port=port)

    # -------- 高层动作：业务调这一个就够 --------

    def ensure_installed_and_running(self) -> dict:
        """保证 xray 已装 + 服务在跑 + 开机自启已设。

        流程（version() 作为统一「是否已装」判断）：
            ① v = version()
            ② 空 → install() → 再 version() 验证（仍空 → VerifyFailedError）
               非空 → was_already = True
            ③ 不管哪条路径都要：
               - 检查服务是否 running，不是就 start，仍不行 → ServiceNotActiveError
               - 检查是否开机自启，不是就 enable，失败 → EnableFailedError

        返回 {"version": str, "was_already_installed": bool, "actions_taken": [str]}。
        失败时抛 XrayError 子类，每个错误消息都附「建议」便于排查。
        """
        actions: list[str] = []

        # ① 统一先查版本号——能拿到版本 = xray 真能跑
        v = self.version()

        if not v:
            # 走全新安装路径
            logger.info("未检测到 xray 版本号，开始全新安装（约 30-60s）")
            self.install()
            actions.append("installed")
            was_already = False

            # 装完再验证一次：拿不到版本号 = 二进制坏了
            v = self.version()
            if not v:
                raise VerifyFailedError(XRAY_VERIFY_FAILED_MESSAGE)
        else:
            # 已装，进入修复路径
            logger.info("检测到 xray 已装 version=%s，进入修复路径", v)
            was_already = True

        # ② 启动前确保 config 不空（空 config 会导致 systemctl start 失败 exit=23）
        if self.is_config_blank():
            logger.info("xray config 为空，写入默认 config（监听 18440 直出）")
            self.write_default_config()
            actions.append("wrote_default_config")

        # ③ 确保服务在线（running）
        if not self.is_running():
            logger.info("xray 服务未 active，尝试 systemctl start xray")
            self.start()
            actions.append("started")
            if not self.is_running():
                raise ServiceNotActiveError(
                    f"{XRAY_SERVICE_NOT_ACTIVE_MESSAGE}（version={v}）"
                )

        # ③ 确保开机自启
        if not self.is_enabled():
            logger.info("xray 未设开机自启，尝试 systemctl enable xray")
            self.enable()
            actions.append("enabled_autostart")

        return {
            "version": v,
            "was_already_installed": was_already,
            "actions_taken": actions,
        }
