"""实机登录与取数验证脚本（本地/HA 容器内均可运行）。

用法：
    python scripts/verify_login.py <手机号> <密码> [--months 2]

仅用于调试：打印登录结果、绑定户号、每日电量/结算账单/余额摘要。
不写任何文件，不依赖 Home Assistant。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp  # noqa: E402

from custom_components.sgcc_power.api import (  # noqa: E402
    SgccPowerAppApi,
    SgccPowerAuthenticationError,
    SgccPowerDeviceVerificationRequired,
    SgccPowerError,
    SgccPowerInteractiveChallengeRequired,
    SgccPowerNetworkError,
)
from custom_components.sgcc_power.synthetic_device import (  # noqa: E402
    build_device_profile,
    create_device_state,
)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("--months", type=int, default=2)
    args = parser.parse_args()

    state = create_device_state()
    profile, state = build_device_profile(state)
    async with aiohttp.ClientSession() as http:
        api = SgccPowerAppApi(
            http,
            username=args.username,
            password=args.password,
            profile=profile,
        )
        try:
            session = await api.async_login()
        except SgccPowerDeviceVerificationRequired as error:
            print(f"[!] 需要新设备短信验证（{error.code}）。")
            code_key = await api.async_send_device_verification_sms()
            code = input("请输入收到的 6 位短信验证码: ").strip()
            session = await api.async_login(verification_code=code, code_key=code_key)
        except SgccPowerInteractiveChallengeRequired as error:
            print(f"[x] 服务端安全校验未通过（{error.code} {error.message}）")
            return 2
        except SgccPowerAuthenticationError as error:
            print(f"[x] 登录失败（{error.code} {error.message}）")
            return 2
        except SgccPowerNetworkError as error:
            print(f"[x] 网络错误：{error}")
            return 3

        print(f"[ok] 登录成功 userId={session.user_id} 户号 {len(api.accounts)} 个")
        for account in api.accounts:
            print(f"  - {account.cons_no_src} {account.name} {account.address or ''}")

        try:
            result = await api.async_query_history(months=args.months)
        except SgccPowerError as error:
            print(f"[x] 取数失败：{error}")
            return 4

        for usage in result.values():
            account = usage.account
            print(f"\n=== {account.cons_no_src} {account.name} ===")
            latest = usage.latest
            if latest:
                print(
                    f"最新日 {latest.day} 总 {latest.usage} kWh "
                    f"(尖 {latest.tip} / 峰 {latest.peak} / 平 {latest.flat} / 谷 {latest.valley})"
                )
            else:
                print("无日电量数据")
            if usage.latest_bill:
                bill = usage.latest_bill
                print(
                    f"最新结算月 {bill.month:%Y-%m} 电量 {bill.usage} kWh 电费 {bill.charge} 元"
                )
            if usage.billing_account:
                balance = usage.billing_account
                print(
                    f"余额 {balance.balance} 应交 {balance.amount_due} "
                    f"(consType={balance.cons_type})"
                )
            if usage.latest_month_meter:
                print(
                    f"抄表 {usage.latest_month_meter.reading} @ {usage.latest_month_meter.day}"
                )
        print("\n[ok] 验证完成")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
