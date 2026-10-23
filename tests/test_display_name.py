"""display_name：内部 ID 形态的 consName 应回退为友好名称。"""

from custom_components.sgcc_power.models import PowerAccount


def test_internal_id_name_falls_back() -> None:
    account = PowerAccount.from_api(
        {
            "id": "99_573e4d00302944ba8c501a437c2b3561_02_osg",
            "powerUserNo": "04302332076092",
            "powerUserNo_dst": "4302332076092",
            "consName": "99_573e4d00302944ba8c501a437c2b3561_02_osg_uc_2610240331_002_6092",
        }
    )
    assert account.display_name == "国家电网 6092"


def test_normal_name_kept() -> None:
    account = PowerAccount.from_api(
        {
            "powerUserNo": "04302332076092",
            "powerUserNo_dst": "4302332076092",
            "consName": "*骞",
        }
    )
    assert account.display_name == "*骞"


def test_missing_name_falls_back() -> None:
    account = PowerAccount.from_api(
        {"powerUserNo": "04302332076092", "powerUserNo_dst": "4302332076092"}
    )
    assert account.display_name == "国家电网 6092"
