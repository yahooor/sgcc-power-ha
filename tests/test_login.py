"""Login payload builders."""

import pytest

from custom_components.sgcc_power.login import (
    LoginMapContext,
    build_device_sms_payload,
    build_login_sms_payload,
    build_password_login_map,
    build_sms_login_map,
    generate_check_code,
    login_header_md5,
    password_md5,
)


def test_password_md5_matches_upstream() -> None:
    assert password_md5("123456") == "e10adc3949ba59abbe56e057f20f883e"


def test_check_code_shape() -> None:
    code = generate_check_code(
        "18574472432", timestamp_ms=1730000000000, random_24="123456789012345678901234"
    )
    # 24 位十进制随机前缀 + AES-ECB 密文 hex（账号+13位时间戳 = 24 字节 → 两块 = 64 字符）
    assert len(code) == 24 + 64
    assert code[:24].isdigit()


def test_password_login_map_structure() -> None:
    params = build_password_login_map(
        "18574472432",
        "secret",
        context=LoginMapContext(province_id="43110"),
        timestamp_ms=1730000000000,
        random_24="123456789012345678901234",
    )
    assert params["quInfo"]["account"] == "18574472432"
    assert params["quInfo"]["password"] == password_md5("secret")
    assert params["quInfo"]["optSys"] == "Android"
    assert params["uscInfo"]["tenant"] == "state_grid"
    assert params["uscInfo"]["member"] == "2202"
    assert params["quInfo"]["code"] == ""
    assert params["quInfo"]["codeKey"] == ""


def test_sms_payload_business_type() -> None:
    context = LoginMapContext()
    assert (
        build_device_sms_payload("13800000000", context)["quInfo"]["businessType"]
        == "logindevice"
    )
    assert (
        build_login_sms_payload("13800000000", context)["quInfo"]["businessType"]
        == "login"
    )


def test_sms_login_map_validation() -> None:
    with pytest.raises(ValueError):
        build_sms_login_map("13800000000", "12345", "key", context=LoginMapContext())
    with pytest.raises(ValueError):
        build_sms_login_map("13800000000", "123456", "", context=LoginMapContext())


def test_login_header_md5_order_independent() -> None:
    a = {"quInfo": {"account": "138", "password": "abc"}, "uscInfo": {"tenant": "sg"}}
    b = {"uscInfo": {"tenant": "sg"}, "quInfo": {"password": "abc", "account": "138"}}
    assert login_header_md5(a) == login_header_md5(b)


def test_build_login_sms_map_contains_code_key() -> None:
    params = build_sms_login_map(
        "13800000000", "654321", "ck-1", context=LoginMapContext()
    )
    assert params["quInfo"]["code"] == "654321"
    assert params["quInfo"]["codeKey"] == "ck-1"
