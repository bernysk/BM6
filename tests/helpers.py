"""Helpers shared by the BM6 tests.

Everything here is written against SPEC.md.  The values below are the values
the spec requires, and are intentionally hard-coded instead of imported so the
tests fail (rather than silently follow the implementation) when the
implementation gets them wrong.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import async_get_platforms
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bm6.const import (
    CONF_BATTERY_TYPE,
    CONF_BATTERY_VOLTAGE,
    CONF_DEVICE_ADDRESS,
    CONF_STATE_ALGORITHM,
    CONF_TEMPERATURE_OFFSET,
    CONF_TEMPERATURE_UNIT,
    CONF_UPDATE_INTERVAL,
    CONF_VOLTAGE_OFFSET,
    DOMAIN,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"

# From SPEC.md section 2.
MISSES_BEFORE_UNREACHABLE = 3
MAX_BACKOFF_UPDATE_INTERVAL = 300
MIN_PLAUSIBLE_VOLTAGE = 5.0

CONFIGURED_UPDATE_INTERVAL = 60
VOLTAGE_OFFSET = 0.1


def entry_data(**overrides) -> dict:
    """Return valid config entry data (see battery.py / config_flow.py)."""
    data = {
        CONF_DEVICE_ADDRESS: ADDRESS,
        CONF_BATTERY_VOLTAGE: "12V",
        CONF_BATTERY_TYPE: "agm",
        CONF_STATE_ALGORITHM: "soc_sod",
        CONF_UPDATE_INTERVAL: CONFIGURED_UPDATE_INTERVAL,
        CONF_VOLTAGE_OFFSET: VOLTAGE_OFFSET,
        CONF_TEMPERATURE_OFFSET: 0.0,
        CONF_TEMPERATURE_UNIT: "°C",
    }
    data.update(overrides)
    return data


def make_entry(**overrides) -> MockConfigEntry:
    """Return a BM6 MockConfigEntry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"BM6 {ADDRESS}",
        unique_id=ADDRESS,
        data=entry_data(**overrides),
    )


def make_reading(
    *,
    voltage: float = 13.2,
    temperature: int = 21,
    percent: int = 88,
    state: int = 0,
    rapid_acceleration: int = 0,
    rapid_deceleration: int = 0,
    rssi: int | None = -60,
    scanner: str | None = "hci0",
) -> SimpleNamespace:
    """Return a fake BM6Data with the attributes the coordinator reads."""
    return SimpleNamespace(
        RealTime=SimpleNamespace(
            Voltage=voltage,
            Temperature=temperature,
            Percent=percent,
            State=state,
            RapidAcceleration=rapid_acceleration,
            RapidDeceleration=rapid_deceleration,
        ),
        Advertisement=SimpleNamespace(RSSI=rssi, Scanner=scanner),
    )


async def async_setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to hass and set it up."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def entity_id_for(hass: HomeAssistant, platform: str, unique_id: str) -> str | None:
    """Return the entity_id registered for a unique_id."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id(platform, DOMAIN, unique_id)


def config_entry_unique_ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    """Return the unique_ids registered for a config entry."""
    registry = er.async_get(hass)
    return {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def fake_coordinator(data, last_good_read=None) -> SimpleNamespace:
    """Return the coordinator surface the entity classes read."""
    return SimpleNamespace(
        data=data,
        device_address=ADDRESS,
        config_entry=SimpleNamespace(data=entry_data()),
        last_good_read=last_good_read,
    )


def entity_object(
    hass: HomeAssistant, platform: str, entity_id: str
) -> Entity | None:
    """Return the live entity object for an entity_id, if it is added."""
    for entity_platform in async_get_platforms(hass, DOMAIN):
        if entity_platform.domain != platform:
            continue
        if entity_id in entity_platform.entities:
            return entity_platform.entities[entity_id]
    return None


async def async_enable_entity(
    hass: HomeAssistant, entry: MockConfigEntry, entity_id: str
) -> None:
    """Enable a disabled-by-default entity and reload the entry."""
    registry = er.async_get(hass)
    assert registry.async_get(entity_id) is not None, f"{entity_id} is not registered"
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
