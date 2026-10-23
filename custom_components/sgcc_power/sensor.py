"""Electricity sensors backed by the App daily-history endpoint.

Sensor set keeps parity with the legacy state_grid integration
(daily / month / year usage with 峰平谷尖 splits, balance, settled bills).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SgccPowerDataCoordinator, SgccPowerRuntimeData
from .models import AccountUsage

KWH = UnitOfEnergy.KILO_WATT_HOUR
CNY = "CNY"


@dataclass(frozen=True, kw_only=True)
class SgccPowerSensorDescription(SensorEntityDescription):
    value_fn: Callable[[AccountUsage], float | None]


SENSORS = (
    SgccPowerSensorDescription(
        key="latest_daily_usage",
        translation_key="latest_daily_usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.latest.usage if usage.latest else None,
    ),
    SgccPowerSensorDescription(
        key="latest_daily_peak",
        translation_key="latest_daily_peak",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.latest.peak if usage.latest else None,
    ),
    SgccPowerSensorDescription(
        key="latest_daily_valley",
        translation_key="latest_daily_valley",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.latest.valley if usage.latest else None,
    ),
    SgccPowerSensorDescription(
        key="latest_daily_flat",
        translation_key="latest_daily_flat",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.latest.flat if usage.latest else None,
    ),
    SgccPowerSensorDescription(
        key="latest_daily_tip",
        translation_key="latest_daily_tip",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.latest.tip if usage.latest else None,
    ),
    SgccPowerSensorDescription(
        key="current_month_usage",
        translation_key="current_month_usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda usage: (
            usage.current_month_total
            if usage.current_month_total is not None
            else usage.month_sum("usage")
        ),
    ),
    SgccPowerSensorDescription(
        key="current_month_peak",
        translation_key="current_month_peak",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.month_sum("peak"),
    ),
    SgccPowerSensorDescription(
        key="current_month_valley",
        translation_key="current_month_valley",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.month_sum("valley"),
    ),
    SgccPowerSensorDescription(
        key="current_month_flat",
        translation_key="current_month_flat",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.month_sum("flat"),
    ),
    SgccPowerSensorDescription(
        key="current_month_tip",
        translation_key="current_month_tip",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.month_sum("tip"),
    ),
    SgccPowerSensorDescription(
        key="latest_month_usage",
        translation_key="latest_month_usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        value_fn=lambda usage: usage.latest_bill.usage if usage.latest_bill else None,
    ),
    SgccPowerSensorDescription(
        key="latest_month_charge",
        translation_key="latest_month_charge",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CNY,
        value_fn=lambda usage: usage.latest_bill.charge if usage.latest_bill else None,
    ),
    SgccPowerSensorDescription(
        key="current_year_usage",
        translation_key="current_year_usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=KWH,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda usage: usage.current_year_usage,
    ),
    SgccPowerSensorDescription(
        key="current_year_charge",
        translation_key="current_year_charge",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CNY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda usage: usage.current_year_charge,
    ),
    SgccPowerSensorDescription(
        key="account_balance",
        translation_key="account_balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CNY,
        value_fn=lambda usage: (
            usage.billing_account.balance if usage.billing_account else None
        ),
    ),
    SgccPowerSensorDescription(
        key="amount_due",
        translation_key="amount_due",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CNY,
        value_fn=lambda usage: (
            usage.billing_account.amount_due if usage.billing_account else None
        ),
    ),
    SgccPowerSensorDescription(
        key="latest_month_meter_reading",
        translation_key="latest_month_meter_reading",
        value_fn=lambda usage: (
            usage.latest_month_meter.reading if usage.latest_month_meter else None
        ),
    ),
)

_TOU_BY_KEY = {
    "latest_daily_peak": "peak",
    "latest_daily_valley": "valley",
    "latest_daily_flat": "flat",
    "latest_daily_tip": "tip",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: SgccPowerRuntimeData = hass.data[DOMAIN][entry.entry_id]
    entities = [
        SgccPowerElectricitySensor(runtime.coordinator, account_id, description)
        for account_id in runtime.coordinator.data
        for description in SENSORS
    ]
    async_add_entities(entities)


class SgccPowerElectricitySensor(
    CoordinatorEntity[SgccPowerDataCoordinator], SensorEntity
):
    """One value calculated from a power account's merged daily history."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SgccPowerDataCoordinator,
        account_id: str,
        description: SgccPowerSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.account_id = account_id
        self.entity_description = description
        self._attr_unique_id = f"{account_id}_{description.key}"

    @property
    def usage(self) -> AccountUsage | None:
        return self.coordinator.data.get(self.account_id)

    @property
    def available(self) -> bool:
        return super().available and self.usage is not None

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.usage) if self.usage else None

    @property
    def device_info(self) -> DeviceInfo | None:
        if not self.usage:
            return None
        account = self.usage.account
        return DeviceInfo(
            identifiers={(DOMAIN, account.account_id)},
            name=f"{account.display_name} {account.cons_no_src[-4:]}",
            manufacturer="国家电网",
            model="网上国网",
            serial_number=account.cons_no_src,
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.usage:
            return None
        account = self.usage.account
        key = self.entity_description.key
        attributes: dict[str, Any] = {
            "account_suffix": account.cons_no_src[-4:],
            "address": account.address,
        }
        if key == "latest_daily_usage":
            attributes["latest_date"] = (
                self.usage.latest.day.isoformat() if self.usage.latest else None
            )
            attributes["daily_history"] = [
                reading.as_dict() for reading in self.usage.readings
            ]
        if key in _TOU_BY_KEY and self.usage.latest:
            attributes["latest_date"] = self.usage.latest.day.isoformat()
        if key in {"latest_month_usage", "latest_month_charge"}:
            attributes["latest_bill_month"] = (
                self.usage.latest_bill.month.strftime("%Y-%m")
                if self.usage.latest_bill
                else None
            )
        if key == "latest_month_usage":
            attributes["monthly_bill_history"] = [
                bill.as_dict() for bill in self.usage.monthly_bills
            ]
        if key in {"current_year_usage", "current_year_charge"}:
            attributes["billing_year"] = self.usage.as_of.year
        if key in {"account_balance", "amount_due"}:
            balance = self.usage.billing_account
            attributes["account_type"] = (
                None
                if balance is None
                else "prepaid"
                if balance.cons_type == "1"
                else "postpaid"
            )
            attributes["balance_date"] = balance.date if balance else None
            attributes["history_owe"] = balance.history_owe if balance else None
            attributes["penalty"] = balance.penalty if balance else None
        if key == "latest_month_meter_reading":
            meter = self.usage.latest_month_meter
            attributes["reading_date"] = meter.day.isoformat() if meter else None
            attributes["transformer_ratio"] = meter.transformer_ratio if meter else None
        return attributes
