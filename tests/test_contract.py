"""协议层契约测试：对真实密钥与真实报文形状回放完整链路。

不依赖 Home Assistant；用生产密钥（const 中的常量）模拟服务端构造响应信封，
验证「SM2/SM3/SM4 信封 → 解密 → 错误码分流 → 会话解析」全链路。
错误响应形状取自上游真实形态（code 顶层 / data.srvrt / data.bizrt）。
"""

import asyncio
import json
from datetime import date

import pytest

from custom_components.sgcc_power import crypto
from custom_components.sgcc_power.api import (
    SgccPowerApiError,
    SgccPowerAppApi,
    SgccPowerAuthenticationError,
    SgccPowerDeviceVerificationRequired,
    SgccPowerInteractiveChallengeRequired,
    SgccPowerNetworkError,
    _app_guid_new,
    _month_period,
    _power_accounts,
    _request_timestamp,
    _srvrt,
    build_daily_usage_payload,
    build_monthly_bills_payload,
)
from custom_components.sgcc_power.const import (
    CLIENT_PRIVATE_KEY_HEX,
    SERVER_PUBLIC_KEY_HEX,
)
from custom_components.sgcc_power.crypto import derive_public_key
from custom_components.sgcc_power.models import PowerAccount
from custom_components.sgcc_power.synthetic_device import (
    build_device_profile,
    create_device_state,
)


def make_api() -> tuple[SgccPowerAppApi, "_CaptureSession"]:
    """构造带生产设备档案的 API 实例与请求捕获型假会话。"""
    profile, _ = build_device_profile(create_device_state())
    session = _CaptureSession()
    api = SgccPowerAppApi(
        session, username="13800000000", password="pw", profile=profile
    )
    return api, session


def server_envelope(payload: dict) -> str:
    """模拟服务端：随机 SM4 密钥加密 + 用客户端公钥包裹（生产密钥路径）。"""
    key_text = crypto.generate_sm4_key_text()
    data = crypto.sm4_encrypt_hex(crypto.compact_json(payload), key_text)
    # 服务端持有的是「客户端公钥」（由客户端私钥推导）
    client_public = derive_public_key(CLIENT_PRIVATE_KEY_HEX)
    resp_key = crypto.sm2_encrypt_key(key_text, client_public)
    return json.dumps({"respKey": resp_key, "encryptData": data}, separators=(",", ":"))


class _CaptureSession:
    """假 aiohttp 会话：捕获请求，返回预置响应。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._responses: list[str] = []

    def queue(self, body: str) -> None:
        self._responses.append(body)

    def queue_payload(self, payload: dict) -> None:
        self.queue(server_envelope(payload))

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append({"url": url, "data": data, "headers": dict(headers or {})})
        outer = self

        class _Ctx:
            async def __aenter__(self_inner):
                return _FakeResponse(200, outer._responses.pop(0))

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


class _FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text


# ---------------------------------------------------------------------------
# 生产密钥信封契约
# ---------------------------------------------------------------------------


def test_production_request_envelope_signature_contract() -> None:
    """请求信封用生产服务器公钥构造后，SM3 签名必须可独立验证。"""
    payload = build_daily_usage_payload(
        _account(), date(2026, 10, 1), date(2026, 10, 31)
    )
    envelope = crypto.build_request_envelope(payload, SERVER_PUBLIC_KEY_HEX)
    assert crypto.verify_request_envelope(envelope)
    # 生产公钥必须是 04 前缀 130 hex（未被上游更换）
    assert SERVER_PUBLIC_KEY_HEX.startswith("04") and len(SERVER_PUBLIC_KEY_HEX) == 130


def test_production_response_envelope_roundtrip() -> None:
    """服务端（真实包裹方式）→ decrypt_response_envelope 的完整回环。"""
    payload = {"code": "1", "data": {"bizrt": {"token": "tok"}}}
    body = json.loads(server_envelope(payload))
    plain = crypto.decrypt_response_envelope(body, CLIENT_PRIVATE_KEY_HEX)
    assert plain == payload


# ---------------------------------------------------------------------------
# 错误码分流契约（真实报文形状）
# ---------------------------------------------------------------------------


def test_error_triage_matrix() -> None:
    """顶层 code / data.srvrt 两种真实错误形态的分流契约。"""
    cases = [
        # (响应, 期望异常, 关键码)
        (
            {"code": "4006", "message": "需要设备验证"},
            SgccPowerDeviceVerificationRequired,
            "4006",
        ),
        (
            {"code": "RK008", "message": "风控"},
            SgccPowerInteractiveChallengeRequired,
            "RK008",
        ),
        ({"code": "-200"}, SgccPowerAuthenticationError, "-200"),
        ({"code": "-201"}, SgccPowerAuthenticationError, "-201"),
        (
            {
                "code": "1",
                "data": {"srvrt": {"resultCode": "500", "resultMessage": "业务失败"}},
            },
            SgccPowerApiError,
            "500",
        ),
        ({"code": "2", "message": "网关拒绝"}, SgccPowerApiError, "2"),
    ]
    for response, expected, code in cases:
        with pytest.raises(expected) as exc_info:
            SgccPowerAppApi._raise_for_error(response)
        assert exc_info.value.code == code, response
    # 成功形态（顶层 1 + srvrt 0000）原样放行
    ok = {"code": "1", "data": {"srvrt": {"resultCode": "0000"}, "bizrt": {}}}
    assert SgccPowerAppApi._raise_for_error(ok) is ok["data"]


def test_srvrt_extraction_shapes() -> None:
    """data.srvrt 缺失/非字典时的真实形状兼容。"""
    assert _srvrt({"data": {"srvrt": {"resultCode": "0000"}}})[0] == "0000"
    assert _srvrt({"data": "string", "message": "m"}) == ("", "m", {})
    assert _srvrt({}) == ("", "", {})
    assert _srvrt({"message": "m"}) == ("", "m", {})


# ---------------------------------------------------------------------------
# 完整登录链路契约（假会话 + 生产加密）
# ---------------------------------------------------------------------------


def _account() -> PowerAccount:
    return PowerAccount.from_api(
        {"powerUserNo": "04302332076092", "powerUserNo_dst": "4302332076092"}
    )


def test_password_login_contract_success() -> None:
    """成功链路：信封请求(签名可验) → 服务端形态响应 → LoginSession 解析。"""
    api, session = make_api()
    session.queue_payload(
        {
            "code": "1",
            "data": {
                "srvrt": {"resultCode": "0000"},
                "bizrt": {
                    "token": "tok-123",
                    "tokenExpireTime": 1296000,
                    "userId": "u-1",
                    "userInfo": [
                        {
                            "userId": "u-1",
                            "addressProvince": "431",
                            "powerUserList": [
                                {
                                    "powerUserNo": "04302332076092",
                                    "powerUserNo_dst": "4302332076092",
                                    "consName": "*骞",
                                    "elecAddr": "某小区",
                                    "phone": "13800000000",
                                }
                            ],
                        }
                    ],
                },
            },
        }
    )
    result = asyncio.run(api.async_login())
    # 会话解析
    assert result.token == "tok-123"
    assert result.user_id == "u-1"
    assert result.expires_at > 0
    # 请求契约：URL/信封结构/签名/登录头
    req = session.requests[0]
    assert "emss-uia-center-front/member/c2/f01" in req["url"]
    envelope = json.loads(req["data"])
    assert crypto.verify_request_envelope(envelope)
    headers = req["headers"]
    assert headers["md5"]  # 登录请求必须带 md5 头
    assert headers["t"] == "" and headers["userid"] == "0"  # 未认证头
    assert headers["appcode"] == "WSGW-SG1001-APP"
    # 敏感信息最小化：手机号不得进入持久化 userInfo
    assert "phone" not in json.dumps(api.login_session.user_info)


def test_password_login_contract_device_verification() -> None:
    """4006 → DeviceVerificationRequired，会话被清空等待验证码重试。"""
    api, session = make_api()
    session.queue_payload({"code": "4006", "message": "新设备需短信验证"})
    with pytest.raises(SgccPowerDeviceVerificationRequired):
        asyncio.run(api.async_login())
    assert api.login_session is None


def test_password_login_contract_auth_error() -> None:
    api, session = make_api()
    session.queue_payload(
        {
            "code": "1",
            "data": {"srvrt": {"resultCode": "1001", "resultMessage": "密码错误"}},
        }
    )
    with pytest.raises(SgccPowerAuthenticationError):
        asyncio.run(api.async_login())


def test_transport_failure_contracts() -> None:
    """HTTP 500 / 非法 JSON / 缺字段响应 → NetworkError（不抛原始异常）。"""
    api, session = make_api()
    session.queue("<html>502 Bad Gateway</html>")
    with pytest.raises(SgccPowerNetworkError):
        asyncio.run(api._async_password_login())

    api, session = make_api()
    session.queue('{"respKey": "zz", "encryptData": "xx"}')  # 无法解密
    with pytest.raises(SgccPowerNetworkError):
        asyncio.run(api._async_password_login())


def test_authenticated_request_headers_contract() -> None:
    """认证请求必须携带 t/userid（令牌前半）与省份头。"""
    api, session = make_api()
    session.queue_payload(
        {
            "code": "1",
            "data": {
                "srvrt": {"resultCode": "0000"},
                "bizrt": {"token": "0123456789abcdef", "userId": "u9"},
            },
        }
    )
    asyncio.run(api.async_login())
    session.queue_payload(
        {"code": "1", "data": {"srvrt": {"resultCode": "0000"}, "list": []}}
    )
    asyncio.run(api.async_query_monthly_bills(_account(), 2026))
    headers = session.requests[-1]["headers"]
    assert headers["t"] == "0123456789abcdef"  # App 通道携带完整令牌
    assert headers["userid"] == "u9"


# ---------------------------------------------------------------------------
# 帮助函数契约
# ---------------------------------------------------------------------------


def test_wire_format_helpers() -> None:
    guid = _app_guid_new()
    assert len(guid) == 60 and guid.isalnum()
    stamp = _request_timestamp()
    assert len(stamp) == 23 and stamp.isdigit()  # 17 位时间 + 6 位随机
    assert _month_period(date(2026, 10, 25), 1) == (date(2026, 9, 1), date(2026, 9, 30))
    assert _month_period(date(2026, 1, 5), 1) == (date(2025, 12, 1), date(2025, 12, 31))


def test_power_accounts_dedup_and_minimize() -> None:
    from custom_components.sgcc_power.api import _minimize_user_info

    accounts = [
        {"powerUserNo": "01", "powerUserNo_dst": "A", "consName": "x", "id": "A"},
        {"powerUserNo": "01", "powerUserNo_dst": "A", "id": "A"},  # 重复户号
        {"foo": "bar"},  # 无户号
    ]
    result = _power_accounts({"powerUserList": accounts})
    assert len(result) == 1 and result[0].cons_no_src == "A"

    minimized = _minimize_user_info(
        {
            "userId": "u",
            "phone": "138",
            "powerUserList": [{"consNo": "c", "secret": "s"}],
        }
    )
    assert "phone" not in minimized
    assert "secret" not in minimized["powerUserList"][0]
    assert minimized["powerUserList"][0]["consNo"] == "c"


def test_payload_shapes_match_wire_contract() -> None:
    """关键业务 payload 的字段形状（与上游接口约定的契约）。"""
    daily = build_daily_usage_payload(_account(), date(2026, 10, 1), date(2026, 10, 31))
    assert daily["serviceCode"] == "BCP_000026"
    assert daily["data"]["consNo"] == "4302332076092"  # 历史接口要求源户号
    assert daily["data"]["startTime"] == "2026-10-01"
    monthly = build_monthly_bills_payload(_account(), 2026)
    assert (
        monthly["data"]["startYm"] == "202601" and monthly["data"]["endYm"] == "202612"
    )
