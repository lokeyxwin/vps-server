"""TC-14-a tools/rgip.py MCP 工具 rgip 适配层。

业务故事:
  agent 调 rgip 工具, MCP handler 透传 9 个参数给 IPProbeWorker.process,
  把工人返回 dict 序列化成 JSON 包成 TextContent 列表返回。

覆盖:
  - TOOL 元数据 (name / description 关键字 / inputSchema 字段集 + 必填集)
  - handler 透传 (含 expire_date 字符串 → date 转换、可选字段默认空)
  - handler 返回形状 (list[TextContent] 含 status JSON)
  - ALL_TOOLS 注册
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from unittest.mock import patch

import pytest

from mcp.types import TextContent

from tools import ALL_TOOLS
from tools import rgip


# ============ TOOL 元数据 ============

def test_tool_name_is_rgip():
    """Tool.name 等于模块名 'rgip'。"""
    assert rgip.TOOL.name == "rgip"


def test_tool_title_present():
    """Tool.title 是人类友好短标题。"""
    assert rgip.TOOL.title
    assert "登记" in rgip.TOOL.title


def test_tool_description_warns_serial():
    """Tool.description 含'一条一条提交'硬约束 (MCP 边界外部串行)。"""
    desc = rgip.TOOL.description
    assert "一条一条" in desc
    assert "串行" in desc or "等上一条" in desc


def test_tool_description_lists_all_statuses():
    """Tool.description 列了 7 种 status 含义, agent 才知道怎么转告用户。"""
    desc = rgip.TOOL.description
    for status in [
        "queued",
        "duplicate",
        "proxy_auth_failed",
        "proxy_timeout",
        "proxy_refused",
        "proxy_failed",
        "probe_vps_unreachable",
    ]:
        assert status in desc, f"description 缺少 status 描述: {status}"


def test_tool_description_mentions_duplicate_egress_field():
    """duplicate 返回里 egress_ip 字段必须在 description 提示 agent。"""
    desc = rgip.TOOL.description
    assert "egress_ip" in desc


def test_input_schema_required_fields():
    """inputSchema.required 必含 5 个上游凭据核心字段。"""
    required = set(rgip.TOOL.inputSchema["required"])
    assert required == {
        "entry_host",
        "entry_port",
        "username",
        "password",
        "protocol",
    }


def test_input_schema_has_nine_fields():
    """inputSchema.properties 含 9 个字段 (5 必填 + 4 选填 含 user_label)。"""
    properties = rgip.TOOL.inputSchema["properties"]
    expected = {
        "entry_host",
        "entry_port",
        "username",
        "password",
        "protocol",
        "declared_egress_ip",
        "provider_domain",
        "expire_date",
        "user_label",
    }
    assert set(properties.keys()) == expected


def test_input_schema_protocol_enum():
    """protocol 字段限定枚举 socks5 / http。"""
    properties = rgip.TOOL.inputSchema["properties"]
    assert properties["protocol"]["enum"] == ["socks5", "http"]


def test_input_schema_entry_port_is_integer():
    """entry_port 类型必须是 integer (不是 string), agent 不该填 '5001'。"""
    properties = rgip.TOOL.inputSchema["properties"]
    assert properties["entry_port"]["type"] == "integer"


def test_input_schema_additional_properties_forbidden():
    """inputSchema 禁止额外字段, 避免 agent 乱塞。"""
    assert rgip.TOOL.inputSchema.get("additionalProperties") is False


def test_tool_annotations_not_read_only():
    """rgip 是写入意图工具, readOnlyHint=False。"""
    assert rgip.TOOL.annotations is not None
    assert rgip.TOOL.annotations.readOnlyHint is False
    assert rgip.TOOL.annotations.destructiveHint is False


# ============ ALL_TOOLS 注册 ============

def test_all_tools_contains_rgip():
    """ALL_TOOLS 包含 (rgip.TOOL, rgip.handler) 一项。"""
    names = [tool.name for tool, _ in ALL_TOOLS]
    assert "rgip" in names
    matching = [
        (tool, handler)
        for tool, handler in ALL_TOOLS
        if tool.name == "rgip"
    ]
    assert len(matching) == 1
    tool, handler = matching[0]
    assert tool is rgip.TOOL
    assert handler is rgip.handler


# ============ handler 透传 ============

def _make_queued_result(ip_id: int = 1, task_id: int = 2) -> dict:
    return {
        "status": "queued",
        "ip_id": ip_id,
        "task_id": task_id,
        "egress_ip": "1.2.3.4",
        "message": "已入库, 后台 worker 会接手挂到生产 VPS",
    }


def test_handler_transparently_forwards_all_required_fields():
    """handler 把 5 个必填参数原样透传给 IPProbeWorker.process。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        asyncio.run(rgip.handler({
            "entry_host": "proxy.example.com",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
        }))
        MockWorker.return_value.process.assert_called_once()
        kwargs = MockWorker.return_value.process.call_args.kwargs
        assert kwargs["entry_host"] == "proxy.example.com"
        assert kwargs["entry_port"] == 5001
        assert kwargs["username"] == "alice"
        assert kwargs["password"] == "secret"
        assert kwargs["protocol"] == "socks5"


def test_handler_coerces_entry_port_to_int():
    """entry_port 如果是字符串数字, handler 强转 int (防 agent 误填)。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": "5001",  # 故意传字符串
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
        }))
        kwargs = MockWorker.return_value.process.call_args.kwargs
        assert kwargs["entry_port"] == 5001
        assert isinstance(kwargs["entry_port"], int)


def test_handler_converts_expire_date_string_to_date_object():
    """expire_date 字符串 'YYYY-MM-DD' 转 datetime.date 对象后传给 worker。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
            "expire_date": "2026-06-15",
        }))
        kwargs = MockWorker.return_value.process.call_args.kwargs
        assert kwargs["expire_date"] == date(2026, 6, 15)


def test_handler_passes_empty_expire_date_as_none():
    """expire_date 留空或缺失时, handler 传 None (符合 process 签名)。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
            "expire_date": "",
        }))
        kwargs = MockWorker.return_value.process.call_args.kwargs
        assert kwargs["expire_date"] is None


def test_handler_forwards_user_label_when_provided():
    """user_label 可选字段被透传, 不影响必填字段。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
            "user_label": "新加坡-机房 A",
        }))
        kwargs = MockWorker.return_value.process.call_args.kwargs
        assert kwargs["user_label"] == "新加坡-机房 A"


def test_handler_defaults_optional_fields_to_empty_string():
    """全部 4 个选填字段缺失时, handler 都传空串 / None 给 process, 不抛错。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
        }))
        kwargs = MockWorker.return_value.process.call_args.kwargs
        assert kwargs["declared_egress_ip"] == ""
        assert kwargs["provider_domain"] == ""
        assert kwargs["expire_date"] is None
        assert kwargs["user_label"] == ""


def test_handler_accepts_none_arguments():
    """arguments=None 时也能跑 (按 None → {} 兜底), 虽然必填缺会 KeyError。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        # None → {} 兜底, 但必填缺会 KeyError, 测的是 None 不直接崩
        with pytest.raises(KeyError):
            asyncio.run(rgip.handler(None))


# ============ handler 返回形状 ============

def test_handler_returns_list_of_one_text_content():
    """handler 返回 list[TextContent], 长度 1。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result()
        result = asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
        }))
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], TextContent)
        assert result[0].type == "text"


def test_handler_returns_json_with_status_queued():
    """成功路径返回 TextContent.text 是 JSON 含 status=queued + ip_id + task_id。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = _make_queued_result(
            ip_id=42, task_id=100,
        )
        result = asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
        }))
        payload = json.loads(result[0].text)
        assert payload["status"] == "queued"
        assert payload["ip_id"] == 42
        assert payload["task_id"] == 100
        assert payload["egress_ip"] == "1.2.3.4"


def test_handler_returns_json_with_status_proxy_auth_failed():
    """失败路径透传, status=proxy_auth_failed 完整返回给 agent。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = {
            "status": "proxy_auth_failed",
            "message": "上游代理 1.2.3.4:5001 密码校验失败...",
        }
        result = asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "wrong",
            "protocol": "socks5",
        }))
        payload = json.loads(result[0].text)
        assert payload["status"] == "proxy_auth_failed"
        assert "密码校验失败" in payload["message"]


def test_handler_returns_json_with_status_duplicate_and_egress_ip():
    """duplicate 路径透传 egress_ip 字段 (agent 转告用户用)。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = {
            "status": "duplicate",
            "egress_ip": "9.9.9.9",
            "message": "这条出口 IP 9.9.9.9 已经在库",
        }
        result = asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
            "declared_egress_ip": "9.9.9.9",
        }))
        payload = json.loads(result[0].text)
        assert payload["status"] == "duplicate"
        assert payload["egress_ip"] == "9.9.9.9"


def test_handler_json_is_chinese_friendly():
    """JSON 用 ensure_ascii=False, 中文文案直接可读, 不变 \\uXXXX。"""
    with patch.object(rgip, "IPProbeWorker") as MockWorker:
        MockWorker.return_value.process.return_value = {
            "status": "proxy_failed",
            "message": "上游代理校验失败: 中文错误信息",
        }
        result = asyncio.run(rgip.handler({
            "entry_host": "1.2.3.4",
            "entry_port": 5001,
            "username": "alice",
            "password": "secret",
            "protocol": "socks5",
        }))
        # 直接含中文, 不是 \uXXXX 编码
        assert "中文错误信息" in result[0].text
