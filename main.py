"""项目统一入口。

用法（在项目根目录跑）：
    uv run python main.py rgvps --ip 1.2.3.4 --user root --pwd 'xxx' --port 22 --ed 2026-12-31
    uv run python main.py xrayinit --ip 1.2.3.4
    uv run python main.py --help

命名约定：
    rgvps     注册 VPS + 装好 xray + 启动 + 自启（一站式）
    xrayinit  在已注册 VPS 上单独装/重装 xray
    rgip      注册代理 IP —— 待实现
    （未来更多业务沿用「动作缩写 + 对象」的命名）
"""

import argparse
import sys
from datetime import date


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式错误，应为 YYYY-MM-DD，收到：{text!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vps-server",
        description="VPS / IP / Proxy 资产管理工具",
    )
    subparsers = parser.add_subparsers(dest="action", required=True, metavar="ACTION")

    # ---------- rgvps：注册 VPS + 全流程装 xray ----------
    p_rgvps = subparsers.add_parser(
        "rgvps",
        help="注册一台 VPS 到数据库并完成 xray 安装/启动/自启",
        description="① 查重 ② SSH 测连 + 采集系统信息 ③ 入库 ④ xray 全流程",
    )
    p_rgvps.add_argument("--ip", required=True, help="服务器 IP")
    p_rgvps.add_argument("--user", required=True, help="登录用户名（通常是 root）")
    p_rgvps.add_argument("--pwd", required=True, help="登录密码")
    p_rgvps.add_argument("--port", type=int, default=22, help="SSH 端口（默认 22）")
    p_rgvps.add_argument(
        "--ed", type=_parse_date, default=None, help="到期日期 YYYY-MM-DD（可选）"
    )
    p_rgvps.add_argument(
        "--provider", default="",
        help="服务商控制台域名（如 aliyun.com / 666clouds.com），用于续费提醒分组",
    )

    # ---------- xrayinit：单独触发 xray 流程 ----------
    p_xrayinit = subparsers.add_parser(
        "xrayinit",
        help="在已注册的 VPS 上单独执行 xray 全流程",
        description="前置：该 IP 必须先经 rgvps 入库。",
    )
    p_xrayinit.add_argument("--ip", required=True, help="目标 VPS 的 IP（必须已入库）")

    # ---------- rgip：登记上游代理 + 部署到 VPS 端口 ----------
    p_rgip = subparsers.add_parser(
        "rgip",
        help="登记一条上游代理 + 部署到一台 VPS 的某个端口",
        description="① 查 IP 表 ② geoip ③ 挑 VPS ④ SSH 部署 + 内 ping ⑤ 写库 ⑥ 外 ping",
    )
    p_rgip.add_argument("--entry-host", required=True, help="上游代理入口地址（域名或 IP）")
    p_rgip.add_argument("--entry-port", type=int, required=True, help="上游代理入口端口")
    p_rgip.add_argument("--user", required=True, help="上游代理账号")
    p_rgip.add_argument("--pwd", required=True, help="上游代理密码")
    p_rgip.add_argument(
        "--protocol", default="socks5", choices=["socks5", "http"],
        help="上游协议（默认 socks5）",
    )
    p_rgip.add_argument("--egress", required=True, help="服务商控制台看到的出口 IP")
    p_rgip.add_argument("--provider", default="", help="服务商域名（可选）")
    p_rgip.add_argument(
        "--ed", type=_parse_date, default=None,
        help="上游到期日 YYYY-MM-DD（可选）",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.action == "rgvps":
        from services.vps_register import register_vps

        result = register_vps(
            ip=args.ip,
            username=args.user,
            password=args.pwd,
            port=args.port,
            expire_date=args.ed,
            provider_domain=args.provider,
        )
        # 成功状态：完整成功 + 部分成功（VPS 入库但 xray 失败）
        success_statuses = {"ok"}
        return 0 if result["status"] in success_statuses else 1

    if args.action == "xrayinit":
        from services.vps_init import init_vps_xray

        result = init_vps_xray(ip=args.ip)
        # 成功状态：ok/imported（内外都通）+ already_running（DB 已是 running）
        # external_unreachable 算半成功——VPS 内部 OK，但外部不通；用 exit code 2 区分
        if result["status"] in ("ok", "imported", "already_running"):
            return 0
        if result["status"] == "external_unreachable":
            return 2
        return 1

    if args.action == "rgip":
        from services.ip_register import register_ip
        import json

        result = register_ip(
            entry_host=args.entry_host,
            entry_port=args.entry_port,
            username=args.user,
            password=args.pwd,
            protocol=args.protocol,
            egress_ip=args.egress,
            provider_domain=args.provider,
            expire_date=args.ed,
        )
        # 末尾打印完整结果（含 node 凭据 + binding ID + ping 状态）
        print("\n========== rgip 结果 ==========")
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        # ok / ok_security_group_blocked 都算成功（节点入库且可用）
        if result["status"] in ("ok", "ok_security_group_blocked"):
            return 0
        return 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
