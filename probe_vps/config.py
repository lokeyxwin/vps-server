"""IPProbeWorker 用的测试 VPS 凭据清单(从系统环境变量读)。

IPProbeWorker (rgip 入口同步段) 触发时, 按顺序从这份清单挑一台测试 VPS,
SSH 上去临时挂用户提交的上游 IP 凭据 (作为 xray outbound) + 内 ping 验证。
连不上挑下一台 (fallback 循环住工人, 不住本文件)。

字段键名与 VPSSession.__init__ 形参对齐 (ip / port / username / password),
工人 VPSSession(**dict) 展开即用。

凭据走系统环境变量 (~/.zshrc.local §04 私有环境变量节, 见 shell-env-management
skill 规约), 不再直写本文件。env 字段命名 (N=1..3, 按需扩):

  PROBE_VPS_N_IP   - 测试 VPS 公网 IP
  PROBE_VPS_N_PORT - SSH 端口 (default 22)
  PROBE_VPS_N_USER - SSH 用户名 (default root)
  PROBE_VPS_N_PWD  - SSH 密码

没有可用测试 VPS 时, 调 get_probe_vps_pool() 会抛 RuntimeError 提示
"请往 ~/.zshrc.local 加 PROBE_VPS_1_* 四条 export 并重启进程", 方便
IPProbeWorker 给 agent 明确指引。

谁用:
- workers/ip_probe_worker.py(通过 get_probe_vps_pool())
- probe_vps/bootstrap.py (ensure_ready 拿 pool 第 N 条)
"""

import os


_MAX_PROBE_SLOTS = 3


def _build_pool() -> tuple[dict, ...]:
    """从环境变量 PROBE_VPS_N_*(N=1.._MAX_PROBE_SLOTS) 拼测试 VPS pool。

    任一编号 IP 为空就跳过该编号(允许中间空, 比如只设 1 和 3)。
    全空返回空 tuple, 上层 get_probe_vps_pool() 看到空就抛 RuntimeError 带指引。
    """
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
    """返回测试 VPS 凭据清单。

    空 pool 时抛 RuntimeError 带指引, 让上层(IPProbeWorker / 调用方)
    能直接把这句话回传给用户/agent, 知道去哪儿加凭据。
    """
    if not PROBE_VPS_POOL:
        raise RuntimeError(NO_PROBE_VPS_MESSAGE)
    return PROBE_VPS_POOL
