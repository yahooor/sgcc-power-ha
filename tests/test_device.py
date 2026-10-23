"""Synthetic device identity and Turing token lifecycle."""

from custom_components.sgcc_power.synthetic_device import (
    build_device_profile,
    create_device_state,
)


def test_profile_valid_and_stable() -> None:
    state = create_device_state()
    profile, updated = build_device_profile(state)
    profile.validate()
    assert updated["token_cache"]["token"].startswith("v3:")
    assert updated["token_cache"]["profile_id"]

    # 缓存窗口内重建：设备身份与令牌保持稳定
    again, _ = build_device_profile(updated)
    assert again.app_guid == profile.app_guid
    assert again.device_token_tx == profile.device_token_tx
    assert again.device_token_tx_time == profile.device_token_tx_time


def test_different_states_yield_different_identities() -> None:
    first, _ = build_device_profile(create_device_state())
    second, _ = build_device_profile(create_device_state())
    assert first.app_guid != second.app_guid
    assert first.device_token_tx != second.device_token_tx
