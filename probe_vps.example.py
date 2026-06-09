"""probe_vps.py 模板(env 化版本)。

凭据走系统环境变量 (~/.zshrc.local §04 私有环境变量节), 不再直写本文件。
本文件保留作"代码骨架参考", probe_vps.py 真本体已 .gitignore 兜底。

实际使用流程:
1. 往 ~/.zshrc.local §04 节追加 4 条 export(N=1..3 按需):
     export PROBE_VPS_1_IP="x.x.x.x"
     export PROBE_VPS_1_PORT="22"
     export PROBE_VPS_1_USER="root"
     export PROBE_VPS_1_PWD="<password>"
2. source ~/.zshrc.local (或新开终端)
3. 启动业务进程 (mcp_server / dev smoke / pytest) 时会读到这些 env

字段键名与 ssh.session.VPSSession.__init__ 形参对齐:
    ip / port / username / password
工人侧: VPSSession(**pool[0]) 展开即用。

PROBE_VPS_POOL 长度 1-3 (由 _MAX_PROBE_SLOTS 控制)。
"""

import os


_MAX_PROBE_SLOTS = 3


def _build_pool() -> tuple[dict, ...]:
    entries: list[dict] = []
    for n in range(1, _MAX_PROBE_SLOTS + 1):
        ip = os.environ.get(f"PROBE_VPS_{n}_IP", "")
        if not ip:
            continue
        entries.append(
            {
                "ip": ip,
                "port": int(os.environ.get(f"PROBE_VPS_{n}_PORT", "22")),
                "username": os.environ.get(f"PROBE_VPS_{n}_USER", "root"),
                "password": os.environ.get(f"PROBE_VPS_{n}_PWD", ""),
            }
        )
    return tuple(entries)


PROBE_VPS_POOL: tuple[dict, ...] = _build_pool()


# IPProbeWorker 在测试 VPS 上临时挂上游凭据用的端口
# 跟测试 VPS 自身默认入口 18440 (xray socks5→freedom 直进直出) 隔离
# 跟生产 VPS 高位段 (>1024 排除清单外) 也隔离
PROBE_TEST_PORT = 19000


NO_PROBE_VPS_MESSAGE = (
    "没有可用测试 VPS。请往 ~/.zshrc.local 的 §04 私有环境变量节追加 "
    "PROBE_VPS_1_IP / PROBE_VPS_1_PORT / PROBE_VPS_1_USER / PROBE_VPS_1_PWD "
    "四条 export (字段对齐 VPSSession.__init__ 形参), 再 source ~/.zshrc.local "
    "或重启 mcp_server 进程后即可生效。"
)


def get_probe_vps_pool() -> tuple[dict, ...]:
    if not PROBE_VPS_POOL:
        raise RuntimeError(NO_PROBE_VPS_MESSAGE)
    return PROBE_VPS_POOL
