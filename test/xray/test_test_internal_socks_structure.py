"""TC-12 xray.service.test_internal_socks 返回 dict 结构断言。

只测 mock 层 (不连真服务器): 验证返回 dict 含老 4 键 + 新 2 键
(exit_code + stderr), 类型和值跟 execute_command 透传一致。
"""

from unittest.mock import patch

import pytest

from xray import service


def _make_execute_result(
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> dict:
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}


def _stdout_with_http_code(http_code: int, body: str = "1.2.3.4") -> str:
    """构造跟 service.test_internal_socks 的 cmd 输出格式一致的 stdout。"""
    return f"__HTTPCODE__{http_code}__BODY__\n{body}"


@pytest.fixture
def fake_client():
    return object()  # service 函数透传给 execute_command, mock 截掉了不实际用


def test_returns_dict_with_all_six_keys(fake_client):
    """ok=True (http 200) 时返回 dict 6 个键齐全, exit_code=0 / stderr=""。"""
    fake = _make_execute_result(
        exit_code=0,
        stdout=_stdout_with_http_code(200, "1.2.3.4"),
        stderr="",
    )
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000)

    assert set(result.keys()) == {
        "ok", "http_code", "body", "error", "exit_code", "stderr",
    }
    assert result["ok"] is True
    assert result["http_code"] == 200
    assert result["body"] == "1.2.3.4"
    assert result["error"] is None
    assert result["exit_code"] == 0
    assert result["stderr"] == ""


def test_exit_code_passthrough_when_curl_refused(fake_client):
    """exit_code=7 (CURLE_COULDNT_CONNECT, socks 端口拒接) 透传。"""
    fake = _make_execute_result(
        exit_code=7,
        stdout="__HTTPCODE__000__BODY__\n",  # curl 失败也写 000 占位
        stderr="",
    )
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000)

    assert result["ok"] is False
    assert result["exit_code"] == 7
    assert isinstance(result["stderr"], str)


def test_exit_code_passthrough_when_curl_timeout(fake_client):
    """exit_code=28 (CURLE_OPERATION_TIMEDOUT) 透传。"""
    fake = _make_execute_result(
        exit_code=28,
        stdout="__HTTPCODE__000__BODY__\n",
        stderr="",
    )
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000)

    assert result["ok"] is False
    assert result["exit_code"] == 28


def test_exit_code_passthrough_when_curl_proxy_auth(fake_client):
    """exit_code=97 (CURLE_PROXY, 常为 socks auth 失败) 透传。"""
    fake = _make_execute_result(
        exit_code=97,
        stdout="__HTTPCODE__000__BODY__\n",
        stderr="",
    )
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000, user="u", pwd="p")

    assert result["ok"] is False
    assert result["exit_code"] == 97


def test_stderr_passthrough_when_paramiko_channel_has_content(fake_client):
    """paramiko 通道 stderr 有内容时, 字段透传 (虽然多数场景为空, 但有内容必透传)。"""
    fake = _make_execute_result(
        exit_code=1,
        stdout="__HTTPCODE__000__BODY__\n",
        stderr="bash: curl: command not found",
    )
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000)

    assert result["stderr"] == "bash: curl: command not found"
    assert result["exit_code"] == 1


def test_legacy_keys_unchanged_when_ok(fake_client):
    """向后兼容: ok/http_code/body/error 4 键含义不变 (XrayWorker / proxy_check 不受影响)。"""
    fake = _make_execute_result(
        exit_code=0,
        stdout=_stdout_with_http_code(200, "8.8.8.8"),
        stderr="",
    )
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000)

    # 跟改动前一致的断言
    assert result["ok"] is True
    assert result["http_code"] == 200
    assert result["body"] == "8.8.8.8"
    assert result["error"] is None


def test_field_types(fake_client):
    """新 2 字段类型固定: exit_code int, stderr str。"""
    fake = _make_execute_result(exit_code=0, stdout=_stdout_with_http_code(200), stderr="")
    with patch.object(service, "execute_command", return_value=fake):
        result = service.test_internal_socks(fake_client, port=19000)

    assert isinstance(result["exit_code"], int)
    assert isinstance(result["stderr"], str)
