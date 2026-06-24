"""分享链接拼装工具（无状态纯函数，ADR-0011 §决策 §8）。

这文件装啥:
  把节点的协议 / 加密方式 / 账密 / 地址端口拼成客户端可一键导入的标准分享 URI。
  当前只覆盖 Shadowsocks 的 SIP002 ``ss://`` 标准（小火箭 / v2rayNG / Clash 通吃）。

工具清单:
  build_ss_url(method, password, host, port, tag)  拼 SIP002 标准 ss:// 链接

谁拿来用:
  db/queries.py 的 _build_proxy_node / list_available_proxies —— 返回节点时附带 share_link，
  agent 一次拿全直接发给用户或让用户扫码导入。
"""

from __future__ import annotations

import base64
from urllib.parse import quote


def build_ss_url(method: str, password: str, host: str, port: int, tag: str = "") -> str:
    """拼 SIP002 标准 ``ss://`` 链接。

    SIP002 形态: ``ss://<base64url(method:password)>@host:port#tag``
    - userinfo 段用 base64url 编码且**去掉 padding**(SIP002 规定 = 号省略)
    - host:port 段明文(不编码)
    - tag 段(节点备注名)用 URL 百分号编码, 空 tag 不带 # 后缀

    参数:
        method: SS 加密方式(如 aes-256-gcm)
        password: 明文密码
        host: 节点地址(VPS 入口 IP 或域名)
        port: 节点端口
        tag: 可选, 节点备注名(导入客户端后显示的名字)

    返回: 完整 ss:// 字符串。
    """
    userinfo = base64.urlsafe_b64encode(
        f"{method}:{password}".encode()
    ).decode().rstrip("=")
    suffix = f"#{quote(tag)}" if tag else ""
    return f"ss://{userinfo}@{host}:{port}{suffix}"
