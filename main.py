"""项目统一入口 — worker 常驻调度 + DB 初始化.

二进程心智模型 (ADR-0008):
  mcp_server.py        前台收单     接 stdio MCP 协议, 分发 5 工具 handler
  main.py worker-loop  后端常驻     扫 task 表 + 推异步段 worker

用法:
  uv run python main.py init-db                       # 首次部署: 建好所有表 (幂等)
  uv run python main.py init-probe-vps [--slot N]     # 装好测试 VPS (xray 装+起+inbound 幂等)
  uv run python main.py worker-loop                   # 启动后端 worker 调度循环

worker-loop 只调度异步段 worker (XrayWorker / ProxyDeployWorker).
SSHWorker / IPProbeWorker 是 MCP 入口工具的同步段, 由 register_vps /
register_ip handler 直接调 process(), 不进 loop.

init-db 说明:
  - 跑 Base.metadata.create_all(engine), CREATE TABLE IF NOT EXISTS 幂等
  - SQLite / MySQL 都生效, 只建表不演化 (后续加字段走迁移)
  - dev SQLite 改 schema: 手动 DROP TABLE 再跑 init-db

init-probe-vps 说明 (ADR-0009):
  - 跑 probe_vps.bootstrap.ensure_ready, 幂等装好测试 VPS xray 基础设施
  - 不入任何 DB 表 (测试机不是业务资产)
  - 何时跑: 首次部署 / 换测试机 / agent 收到 probe_vps_not_ready 时
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

import config
from log import get_logger


logger = get_logger("main.worker_loop")

_stop = False


def _install_signal_handlers() -> None:
    """SIGTERM / SIGINT 都置 _stop=True, 让 worker loop 下一轮检测后退出."""
    def _handler(signum, _frame):
        global _stop
        _stop = True
        logger.info("main.worker-loop 收到信号 %s, 准备优雅退出", signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _run_worker_loop() -> int:
    """串行调度异步段 worker (XrayWorker → ProxyDeployWorker), idle 时 sleep."""
    from workers.proxy_deploy_worker import ProxyDeployWorker
    from workers.xray_worker import XrayWorker

    xray_worker = XrayWorker()
    proxy_worker = ProxyDeployWorker()

    logger.info(
        "main.worker-loop 启动: poll_interval=%ds, workers=[XrayWorker, ProxyDeployWorker]",
        config.POLL_INTERVAL_SECONDS,
    )

    while not _stop:
        busy = 0
        try:
            busy += xray_worker.run_once()
        except Exception as exc:  # noqa: BLE001 — worker 自身异常不杀死循环
            logger.warning(
                "XrayWorker.run_once 抛错: %s: %s", type(exc).__name__, exc,
            )
        if _stop:
            break
        try:
            busy += proxy_worker.run_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ProxyDeployWorker.run_once 抛错: %s: %s",
                type(exc).__name__, exc,
            )

        if not busy and not _stop:
            time.sleep(config.POLL_INTERVAL_SECONDS)

    logger.info("main.worker-loop 已退出")
    return 0


def _init_db() -> int:
    """跑 Base.metadata.create_all(engine), 建好所有表 (幂等).

    依赖 db.models 里所有 ORM 类都已 import 注册到 Base.metadata. 实际靠
    db/__init__.py 顶部 import models 完成.
    """
    import db  # noqa: F401 — 触发 db/__init__.py 注册所有 ORM 表
    from db.base import Base
    from db.engine import engine

    logger.info(
        "init-db 启动: db_url=%s, tables=%s",
        engine.url,
        sorted(Base.metadata.tables.keys()),
    )
    Base.metadata.create_all(engine)
    logger.info("init-db 完成: %d 张表已就绪", len(Base.metadata.tables))
    return 0


def _init_probe_vps(slot: int = 0) -> int:
    """跑 probe_vps.bootstrap.ensure_ready, 幂等装好测试 VPS (ADR-0009).

    slot 选 PROBE_VPS_POOL 第几条 (0-based); pool 空 / 越界 / setup 失败都退 1.
    """
    from probe_vps import (
        ProbeVPSError,
        bootstrap,
        get_probe_vps_pool,
    )

    logger.info("init-probe-vps 启动: slot=%d", slot)
    try:
        pool = get_probe_vps_pool()
    except RuntimeError as exc:
        logger.error("init-probe-vps: pool 空 → %s", exc)
        return 1
    if slot < 0 or slot >= len(pool):
        logger.error(
            "init-probe-vps: slot=%d 越界 (pool 长度=%d)", slot, len(pool),
        )
        return 1

    entry = pool[slot]
    try:
        handle = bootstrap.ensure_ready(entry)
    except ProbeVPSError as exc:
        logger.error(
            "init-probe-vps 失败: %s: %s", type(exc).__name__, exc,
        )
        return 1

    logger.info(
        "init-probe-vps 完成: host=%s inbound_port=%d",
        handle.host, handle.inbound_port,
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vps-server",
        description="VPS / IP / Proxy 资产管理 — 后端 worker 调度入口",
    )
    subparsers = parser.add_subparsers(dest="action", required=True, metavar="ACTION")
    subparsers.add_parser(
        "init-db",
        help="建好所有表 (幂等, 首次部署或加新表时跑一次)",
    )
    init_probe_parser = subparsers.add_parser(
        "init-probe-vps",
        help="装好测试 VPS xray (幂等, ADR-0009)",
    )
    init_probe_parser.add_argument(
        "--slot", type=int, default=0,
        help="选 PROBE_VPS_POOL 第几条 (0-based, default 0)",
    )
    subparsers.add_parser(
        "worker-loop",
        help="启动 worker 调度循环 (常驻进程)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.action == "init-db":
        return _init_db()
    if args.action == "init-probe-vps":
        return _init_probe_vps(slot=args.slot)
    if args.action == "worker-loop":
        _install_signal_handlers()
        return _run_worker_loop()
    return 2


if __name__ == "__main__":
    sys.exit(main())
