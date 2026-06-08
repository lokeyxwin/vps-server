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


def _parse_outbounds_from_config(cfg: dict) -> list[dict]:
    """从 xray config dict 抠出每条 inbound 关联的出口信息。

    跟 xray.config.extract_port_bindings 的区别:
      - 这里**包含**所有 inbound (含 default-direct), 因为直进直出条目也要返回
        (XrayWorker 据此判断是否已有 socks5→freedom 默认入口)
      - 加 outbound_protocol 字段 (freedom / socks / http / ...) 给纳管路径判定
      - 把 inbound_protocol 也单独返回(用 xray 字面值, 比如 "socks")
      - 把 outbound 的 server (upstream) 也单独抠 host/port/user/pwd

    纯字典操作, 没 SSH 没 IO。
    """
    inbounds = cfg.get("inbounds", []) or []
    outbounds = cfg.get("outbounds", []) or []
    rules = (cfg.get("routing", {}) or {}).get("rules", []) or []

    routing_map: dict[str, str] = {}
    for rule in rules:
        in_tags = rule.get("inboundTag", []) or []
        out_tag = rule.get("outboundTag", "")
        for tag in in_tags:
            routing_map[tag] = out_tag

    outbound_by_tag = {ob.get("tag", ""): ob for ob in outbounds}

    result: list[dict] = []
    for inb in inbounds:
        tag = inb.get("tag", "")
        port = inb.get("port", 0)
        inbound_protocol = inb.get("protocol", "")

        accounts = (inb.get("settings", {}) or {}).get("accounts", []) or []
        if accounts:
            inbound_user = accounts[0].get("user", "")
            inbound_pwd = accounts[0].get("pass", "")
        else:
            inbound_user = ""
            inbound_pwd = ""

        out_tag = routing_map.get(tag, "")
        outbound = outbound_by_tag.get(out_tag, {}) or {}
        outbound_protocol = outbound.get("protocol", "")

        servers = (outbound.get("settings", {}) or {}).get("servers", []) or []
        first_server = servers[0] if servers else {}
        upstream_host = first_server.get("address", "")
        upstream_port = first_server.get("port", 0) or 0
        server_users = first_server.get("users", []) or []
        if server_users:
            upstream_user = server_users[0].get("user", "")
            upstream_pwd = server_users[0].get("pass", "")
        else:
            upstream_user = ""
            upstream_pwd = ""

        meta = outbound.get("_meta", {}) or {}
        egress_ip = meta.get("egress_ip", "")
        egress_country = meta.get("egress_country", "")

        result.append({
            "vps_port": port,
            "inbound_protocol": inbound_protocol,
            "inbound_user": inbound_user,
            "inbound_pwd": inbound_pwd,
            "outbound_protocol": outbound_protocol,
            "upstream_host": upstream_host,
            "upstream_port": upstream_port,
            "upstream_user": upstream_user,
            "upstream_pwd": upstream_pwd,
            "egress_ip": egress_ip,
            "egress_country": egress_country,
        })
    return result


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

    def extract_existing_outbounds(self) -> list[dict]:
        """抠出现有出口配置(纳管核心,⭐ 抠信息类)。

        扫 xray 配置里的 inbound + 路由 + outbound,按"直进直出 vs 代理出口"分类,
        返回 list[dict]。空配置 → 返回 [] 不抛错。

        每条字段:
            vps_port: int            服务器上的端口号 (inbound 监听端口)
            inbound_protocol: str    入口协议: 通常是 "socks" / "socks5"
            inbound_user: str        入口账号 (noauth 时空串)
            inbound_pwd: str         入口密码 (明文,内部用;noauth 时空串)
            outbound_protocol: str   ⭐ outbound 协议,决定走"直进直出"还是"纳管":
                                       "freedom"          → 直进直出,XrayWorker 跳过纳管
                                       "socks" / "socks5" → 代理出口,XrayWorker 走内 ping + 写库/remove
                                       其他                → 兜底按"非直进直出"处理
            upstream_host: str       上游入口域名/IP (freedom 时空串)
            upstream_port: int       上游入口端口 (freedom 时 0)
            upstream_user: str       上游账号 (freedom 时空串)
            upstream_pwd: str        上游密码 (freedom 时空串)
            egress_ip: str           出口 IP (从 outbound 备注读,无则 "")
            egress_country: str      出口国家 (同上,无则 "")

        字段命名细节见 test/xray_worker/spec.md v5 §4 + §二。
        """
        if xc.is_config_blank(self.client):
            return []
        try:
            cfg = xc.read_config(self.client)
        except xc.ConfigReadError:
            return []
        return _parse_outbounds_from_config(cfg)

    def test_internal_socks(
        self, port: int = xc.DEFAULT_PORT, user: str = "", pwd: str = "",
    ) -> dict:
        """在服务器内部测试 socks5 代理。

        port: 测哪个 inbound（默认 18440 = default-direct noauth）
        user/pwd: 该 inbound 的 socks5 账密；rgIP 部署的端口需要传，
                 default-direct (18440) 是 noauth 留空即可

        返回 dict 含 ok / http_code / body / error / exit_code / stderr。
        后两者 (exit_code + stderr) 供 IPProbeWorker 失败分类用,
        详细字段语义见 service.test_internal_socks docstring。
        """
        return service.test_internal_socks(self.client, port=port, user=user, pwd=pwd)

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
