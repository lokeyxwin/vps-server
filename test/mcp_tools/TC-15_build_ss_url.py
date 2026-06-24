"""TC-15 build_ss_url —— SIP002 标准 ss:// 链接纯函数.

验收金标准: ADR-0011 §决策 §8 + task/doing_29_*.md §3
工具位置: toolbox/share_link.py

SIP002 形态: ss://<base64url(method:password) 无 padding>@host:port#tag(urlencode)

子测:
  TC-15-a 标准串完全匹配预期(base64url 无 padding / host:port 明文 / #tag)
  TC-15-b base64url userinfo 段无 padding(不含 '=')
  TC-15-c userinfo base64url 解码往返 == 'method:password'(含特殊字符密码)
  TC-15-d 特殊字符 tag 走 urlencode(空格→%20)
  TC-15-e 空 tag 不带 # 后缀
"""

from __future__ import annotations

import base64

from toolbox.share_link import build_ss_url


# ============================================================
# TC-15-a 标准串完全匹配(手算金标准)
# ============================================================

def test_standard_url_exact_match():
    """SIP002 标准: ss://YWVzLTI1Ni1nY206cHdk@1.2.3.4:8388#SG-42."""
    url = build_ss_url("aes-256-gcm", "pwd", "1.2.3.4", 8388, "SG-42")
    assert url == "ss://YWVzLTI1Ni1nY206cHdk@1.2.3.4:8388#SG-42"


# ============================================================
# TC-15-b base64url userinfo 无 padding
# ============================================================

def test_userinfo_base64url_no_padding():
    """userinfo 段(@ 前 ss:// 后)必须是 base64url 且不含 padding '='."""
    # method:password 长度故意制造 base64 需要 padding 的情形
    url = build_ss_url("aes-256-gcm", "p", "h", 1, "")
    userinfo = url.removeprefix("ss://").split("@", 1)[0]
    assert "=" not in userinfo, f"userinfo 不应含 padding: {userinfo!r}"


# ============================================================
# TC-15-c userinfo 往返解码 == method:password(特殊字符密码)
# ============================================================

def test_userinfo_roundtrip_special_password():
    """密码含 @ : / # 等特殊字符也走 base64 进 userinfo, 解码能完整还原."""
    method = "aes-256-gcm"
    password = "p@ss:w/rd#"
    url = build_ss_url(method, password, "203.0.113.10", 18441, "")

    userinfo = url.removeprefix("ss://").split("@", 1)[0]
    pad = "=" * (-len(userinfo) % 4)
    decoded = base64.urlsafe_b64decode(userinfo + pad).decode()

    assert decoded == f"{method}:{password}"
    # 特殊字符没泄漏到 userinfo 明文里(都被 base64 包住)
    for ch in ("@", ":", "/", "#"):
        assert ch not in userinfo


# ============================================================
# TC-15-d host:port 明文 + tag urlencode
# ============================================================

def test_host_port_plain_and_tag_urlencoded():
    """host:port 段明文(不编码), tag 走 urlencode(空格→%20)."""
    url = build_ss_url("aes-256-gcm", "pwd", "example.com", 443, "新加坡 节点")
    # host:port 明文可见
    assert "@example.com:443#" in url
    # tag 段空格被编码成 %20, 不出现裸空格
    tag_part = url.split("#", 1)[1]
    assert " " not in tag_part
    assert "%20" in tag_part


# ============================================================
# TC-15-e 空 tag 不带 # 后缀
# ============================================================

def test_empty_tag_no_hash_suffix():
    """tag 为空串时, 链接末尾不带 #."""
    url = build_ss_url("aes-256-gcm", "pwd", "1.2.3.4", 8388, "")
    assert "#" not in url
    assert url.endswith(":8388")
