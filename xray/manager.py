"""XrayManager 类：封装一个 SSH 连接上的 xray 生命周期。

每个方法是 atom 的薄包装，外加一个高层 ensure_installed_and_running，
让业务层只调一个方法就把整个「装 + 启 + 自启」流程走完。
"""

from __future__ import annotations

import paramiko

from log import get_logger
from xray import service
from xray import config as xc
from xray.service import (
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
        return service.is_installed(self.client)

    def is_running(self) -> bool:
        return service.is_running(self.client)

    def is_enabled(self) -> bool:
        return service.is_enabled(self.client)

    def version(self) -> str:
        return service.version(self.client)

    # -------- 操作类（直接代理 atom，错误向上抛）--------

    def install(self) -> None:
        service.install(self.client)

    def uninstall(self) -> None:
        service.uninstall(self.client)

    def start(self) -> None:
        service.start(self.client)

    def stop(self) -> None:
        service.stop(self.client)

    def enable(self) -> None:
        service.enable(self.client)

    def disable(self) -> None:
        service.disable(self.client)

    def reload(self) -> None:
        service.reload(self.client)

    # -------- 配置层（代理到 xray.config）--------

    def is_config_blank(self) -> bool:
        return xc.is_config_blank(self.client)

    def write_default_config(self) -> None:
        xc.write_default_config(self.client)

    def upload_config(self, config_dict: dict) -> None:
        xc.upload_config(self.client, config_dict)

    def validate_config(self) -> None:
        xc.validate_config(self.client)

    def import_existing_bindings(self) -> list[dict]:
        """复合操作：读取服务器现行 config + 抽出"已部署的客户端 inbound 绑定"列表。

        空 config / 文件缺失 → 返回 []（避免无谓的 read_config）
        有 config → read_config + extract_port_bindings 一气呵成

        业务用法：vps_init 重装时，需要把已挂在 xray 上的代理出口端口"扣掉"
        免得当成空闲重新分配；同时把这些绑定信息抄录到 proxy 表。
        """
        if xc.is_config_blank(self.client):
            return []
        return xc.extract_port_bindings(xc.read_config(self.client))

    def test_internal_socks(self, port: int = xc.DEFAULT_PORT) -> dict:
        """在服务器内部测试 socks5 代理（默认 18440）。返回结果字典。"""
        return service.test_internal_socks(self.client, port=port)

    # -------- 复合动作：给 binding 类业务（rgIP / 巡检 / admin MCP）一行调 --------

    def apply_proxy_binding(
        self,
        vps_port: int,
        proxy_outbound: dict,
        inbound_user: str,
        inbound_pwd: str,
    ) -> dict:
        """往现有 xray config 增量加一组 binding 三件套并落到服务器上。

        编排：read_config → add_proxy_binding → upload_config → validate_config → reload

        参数同 xc.add_proxy_binding（透传）。
        返回追加后的完整 config dict —— 业务侧拿这个 dict 备份，失败时配合
        rollback_proxy_binding 撤回。

        抛错（透传 atom 层，业务侧负责捕获并转 status）：
            PortConflictError / PortAlreadyBoundError / OutboundTagConflictError
                ← add 阶段
            ConfigWriteError                                                          ← upload 阶段
            ConfigValidationError                                                     ← validate 阶段
            ReloadFailedError                                                         ← reload 阶段
        """
        logger.info(
            "XrayManager.apply_proxy_binding: vps_port=%s outbound_tag=%s → applying",
            vps_port, proxy_outbound.get("tag", "?"),
        )
        current = xc.read_config(self.client)
        new_config = xc.add_proxy_binding(
            current, vps_port, proxy_outbound, inbound_user, inbound_pwd,
        )
        xc.upload_config(self.client, new_config)
        xc.validate_config(self.client)
        service.reload(self.client)
        logger.info(
            "XrayManager.apply_proxy_binding: vps_port=%s → ok",
            vps_port,
        )
        return new_config

    def replace_proxy_binding(
        self,
        vps_port: int,
        proxy_outbound: dict,
        inbound_user: str,
        inbound_pwd: str,
    ) -> dict:
        """原地替换某 vps_port 上的 binding（先 remove 旧的，再 add 新的）。

        使用场景：proxy_record 里 vps_port 已被 expired IP 占着，新 IP 要顶替这个端口。
        xray config 里该 vps_port 对应的旧三件套（client-{port} inbound + 旧 outbound
        + 旧 routing 规则）会被完整替换为新三件套。

        编排：read_config → remove_proxy_binding → add_proxy_binding
              → upload_config → validate_config → reload

        幂等：如果 vps_port 当前在 config 里不存在（remove 静默 noop），效果等同纯 add。

        参数同 apply_proxy_binding。
        返回追加后的完整 config dict（业务备份用）。

        抛错（同 apply_proxy_binding：透传 atom 错误，业务侧捕获转 status）。
        """
        logger.info(
            "XrayManager.replace_proxy_binding: vps_port=%s new_outbound_tag=%s → replacing",
            vps_port, proxy_outbound.get("tag", "?"),
        )
        current = xc.read_config(self.client)
        after_remove = xc.remove_proxy_binding(current, vps_port)
        new_config = xc.add_proxy_binding(
            after_remove, vps_port, proxy_outbound, inbound_user, inbound_pwd,
        )
        xc.upload_config(self.client, new_config)
        xc.validate_config(self.client)
        service.reload(self.client)
        logger.info(
            "XrayManager.replace_proxy_binding: vps_port=%s → ok",
            vps_port,
        )
        return new_config

    def rollback_proxy_binding(self, vps_port: int, last_config: dict) -> None:
        """撤回一组 binding：从 last_config 删 + 上传 + reload。

        参数：
            vps_port    : 要撤回的端口
            last_config : apply_proxy_binding 返回的 config dict（已含目标 binding）

        幂等：如果 last_config 里没有该 binding 也不会抛错（remove_proxy_binding 静默 noop）；
        但仍然会触发一次 upload + reload —— 业务侧只在确实需要回滚时调用即可。

        不调 validate（remove 后的 config 拓扑只少不多，xray 校验过的 baseline 减条规则不会破坏语法）。
        """
        logger.info(
            "XrayManager.rollback_proxy_binding: vps_port=%s → rolling back",
            vps_port,
        )
        rolled = xc.remove_proxy_binding(last_config, vps_port)
        xc.upload_config(self.client, rolled)
        service.reload(self.client)
        logger.info(
            "XrayManager.rollback_proxy_binding: vps_port=%s → ok",
            vps_port,
        )

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
            logger.info("XrayManager.version: → '' (not installed) → installing... (~30-60s)")
            self.install()
            actions.append("installed")
            was_already = False

            # 装完再验证一次：拿不到版本号 = 二进制坏了
            v = self.version()
            if not v:
                raise VerifyFailedError(XRAY_VERIFY_FAILED_MESSAGE)
        else:
            # 已装，进入修复路径
            logger.info("XrayManager.version: → %s (already installed)", v)
            was_already = True

        # ② 启动前确保 config 不空（空 config 会导致 systemctl start 失败 exit=23）
        if self.is_config_blank():
            logger.info("XrayManager.is_config_blank: → True → write_default_config")
            self.write_default_config()
            actions.append("wrote_default_config")

        # ③ 确保服务在线（running）
        if not self.is_running():
            logger.info("XrayManager.is_running: → False → systemctl start xray")
            self.start()
            actions.append("started")
            if not self.is_running():
                raise ServiceNotActiveError(
                    f"{XRAY_SERVICE_NOT_ACTIVE_MESSAGE}（version={v}）"
                )

        # ③ 确保开机自启
        if not self.is_enabled():
            logger.info("XrayManager.is_enabled: → False → systemctl enable xray")
            self.enable()
            actions.append("enabled_autostart")

        return {
            "version": v,
            "was_already_installed": was_already,
            "actions_taken": actions,
        }
