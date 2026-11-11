"""Connectivity diagnostic for BM6 monitors."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, TRANSLATION_KEY_REACHABLE
from .coordinator import BM6DataUpdateCoordinator


if TYPE_CHECKING:
    from . import BM6ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: BM6ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the connectivity diagnostic."""
    async_add_entities([BM6ReachableBinarySensor(config_entry.runtime_data.coordinator)])


class BM6ReachableBinarySensor(CoordinatorEntity[BM6DataUpdateCoordinator], BinarySensorEntity):
    """Indicate whether recent read attempts have succeeded."""

    _attr_has_entity_name = True
    _attr_translation_key = TRANSLATION_KEY_REACHABLE
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BM6DataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._device_address = coordinator.device_address

    @property
    def unique_id(self) -> str:
        """Return a stable identifier for the connectivity entity."""
        return f"{DOMAIN}_{self._attr_translation_key.lower()}_{self._device_address}"

    @property
    def device_info(self) -> DeviceInfo:
        """Associate the entity with its BM6 device."""
        return DeviceInfo(identifiers={(DOMAIN, self._device_address)},
                          name=f"BM6 {self._device_address}", manufacturer="BM6",
                          model="Battery Monitor BM6")

    @property
    def available(self) -> bool:
        """Keep connectivity visible even when the monitor is unreachable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether recent read attempts have succeeded."""
        return self.coordinator.reachable
