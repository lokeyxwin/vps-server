"""probe_vps.py 模板。

实际使用时:
1. cp probe_vps.example.py probe_vps.py
2. 在 probe_vps.py 里替换占位为真实凭据
3. probe_vps.py 已在 .gitignore, 不会进 git

字段键名与 ssh.session.VPSSession.__init__ 形参对齐:
    ip / port / username / password
工人侧: VPSSession(**pool[0]) 展开即用。

PROBE_VPS_POOL 长度 1-3。
"""


PROBE_VPS_POOL: tuple[dict, ...] = (
    {
        "ip": "PLACEHOLDER_HOST_1",
        "port": 22,
        "username": "root",
        "password": "PLACEHOLDER_PASSWORD_1",
    },
)


NO_PROBE_VPS_MESSAGE = (
    "没有可用测试 VPS。请往 probe_vps.py 的 PROBE_VPS_POOL 加一条凭据 "
    "(字段: ip / port / username / password, 跟 VPSSession.__init__ 形参对齐)。"
)


def get_probe_vps_pool() -> tuple[dict, ...]:
    if not PROBE_VPS_POOL:
        raise RuntimeError(NO_PROBE_VPS_MESSAGE)
    return PROBE_VPS_POOL
