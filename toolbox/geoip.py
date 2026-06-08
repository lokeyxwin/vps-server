"""IP 归属地查询 atom（ipinfo.io）。

rgIP 业务在登记新 IP 时调用一次，把权威的国家/城市/地区信息落库到 ip_record。
查询失败不阻断业务：返回全空字段 + 日志告警，业务正常继续，落库字段为空串。

无 IPINFO_TOKEN 也能跑（匿名模式 + warning），但额度很小，建议配 token。
"""

from __future__ import annotations

import requests

import config
from log import get_logger


logger = get_logger(__name__)


# ipinfo.io 免费层只返回 country ISO 码，country_name 需要本地维护映射
# 常见国家先列在这里，覆盖业务上 80% 的代理出口；缺失的留空串，
# 后续如发现高频缺失再回头补
_ISO_TO_COUNTRY_NAME = {
    "US": "United States",
    "SG": "Singapore",
    "HK": "Hong Kong",
    "JP": "Japan",
    "KR": "South Korea",
    "TW": "Taiwan",
    "GB": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "NL": "Netherlands",
    "CA": "Canada",
    "AU": "Australia",
    "RU": "Russia",
    "IN": "India",
    "ID": "Indonesia",
    "VN": "Vietnam",
    "TH": "Thailand",
    "MY": "Malaysia",
    "PH": "Philippines",
    "TR": "Turkey",
    "BR": "Brazil",
    "MX": "Mexico",
    "CN": "China",
}


def _empty_result(reason: str = "") -> dict:
    """统一的失败兜底结果。reason 不入返回值，只走日志。"""
    return {
        "country_code": "",
        "country_name": "",
        "city": "",
        "region_name": "",
        "raw": None,
    }


def lookup_egress(ip: str) -> dict:
    """查询 IP 的地理归属信息。

    返回:
        {
            "country_code": str,   # ISO 2 字母，例 'US' / 'SG'；失败为 ''
            "country_name": str,   # 例 'United States'；ipinfo 不直接给，靠本地映射
            "city": str,           # 例 'Los Angeles'；失败 / 缺失为 ''
            "region_name": str,    # 例 'California'；失败 / 缺失为 ''
            "raw": dict | None,    # 原始 API 响应（含 loc/org/timezone 等），调试用
        }

    失败兜底（网络错 / 429 / JSON 解析失败 / bogon IP）→ 返回 _empty_result + 日志 warning，
    不抛异常（业务可以继续，落库字段为空串）。
    """
    if not ip:
        logger.warning("lookup_egress: ip='' → empty (caller passed empty IP)")
        return _empty_result("empty input")

    url = f"https://ipinfo.io/{ip}"
    params = {}
    if config.IPINFO_TOKEN:
        params["token"] = config.IPINFO_TOKEN
    else:
        logger.warning(
            "lookup_egress: ip=%s → no IPINFO_TOKEN (anonymous mode, very low quota)",
            ip,
        )

    try:
        r = requests.get(url, params=params, timeout=config.IPINFO_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — 网络/SSL/DNS 都归一为失败兜底
        logger.warning(
            "lookup_egress: ip=%s → network_error=%s (%s)",
            ip, type(exc).__name__, exc,
        )
        return _empty_result("network error")

    if r.status_code == 429:
        logger.warning("lookup_egress: ip=%s → http=429 rate_limited", ip)
        return _empty_result("rate limited")

    if r.status_code != 200:
        logger.warning(
            "lookup_egress: ip=%s → http=%s body=%s",
            ip, r.status_code, (r.text or "")[:120],
        )
        return _empty_result(f"http {r.status_code}")

    try:
        data = r.json()
    except ValueError as exc:
        logger.warning(
            "lookup_egress: ip=%s → json_decode_error=%s body=%s",
            ip, exc, (r.text or "")[:120],
        )
        return _empty_result("json decode error")

    # ipinfo.io 对私有 IP / bogon 返回 {"ip": "...", "bogon": true}
    if data.get("bogon"):
        logger.warning("lookup_egress: ip=%s → bogon=true (private/reserved range)", ip)
        return _empty_result("bogon ip")

    country_code = (data.get("country") or "").strip().upper()
    city = (data.get("city") or "").strip()
    region_name = (data.get("region") or "").strip()
    country_name = _ISO_TO_COUNTRY_NAME.get(country_code, "")

    logger.info(
        "lookup_egress: ip=%s → country=%s city=%s region=%s",
        ip, country_code or "?", city or "?", region_name or "?",
    )

    return {
        "country_code": country_code,
        "country_name": country_name,
        "city": city,
        "region_name": region_name,
        "raw": data,
    }
