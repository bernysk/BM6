"""Translation tests: every language file mirrors ``en.json`` (SPEC section 6/7)."""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homeassistant.components.sensor import SensorDeviceClass

from custom_components.bm6 import const, device_trigger
from custom_components.bm6.battery import (
    BatteryState,
    BatteryStateAlgorithm,
    BatteryType,
    BatteryVoltage,
)
from .helpers import ADDRESS, entry_data

TRANSLATIONS_DIR = (
    Path(__file__).parent.parent / "custom_components" / "bm6" / "translations"
)
TRANSLATION_FILES = sorted(TRANSLATIONS_DIR.glob("*.json"))


def load_translation(path: Path) -> dict[str, Any]:
    """Load one translation file."""
    return json.loads(path.read_text(encoding="utf-8"))


EN = load_translation(TRANSLATIONS_DIR / "en.json")


def key_paths(obj: Any, prefix: str = "") -> set[str]:
    """Return every key path of a nested dict, including empty branches."""
    paths: set[str] = {prefix} if prefix else set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            paths |= key_paths(value, f"{prefix}.{key}" if prefix else key)
    return paths


def get_path(obj: Any, path: str) -> Any:
    """Return the value at a dotted path."""
    for part in path.split("."):
        obj = obj[part]
    return obj


def has_path(obj: Any, path: str) -> bool:
    """Return True when the dotted path exists."""
    try:
        get_path(obj, path)
    except (KeyError, TypeError):
        return False
    return True


def translation_key_of(entity_class: type) -> str | None:
    """Return the entity class's translation key.

    Recent Home Assistant versions turn ``_attr_*`` class attributes into
    descriptors, so the value is only readable on an instance.
    """
    try:
        instance = entity_class.__new__(entity_class)
    except Exception:  # not an entity class we can probe
        return None
    value = getattr(instance, "_attr_translation_key", None)
    return value if isinstance(value, str) else None


def class_translation_keys(module_name: str) -> dict[str, str]:
    """Return ``{translation_key: class_name}`` for entity classes in a module."""
    module = importlib.import_module(f"custom_components.bm6.{module_name}")
    keys: dict[str, str] = {}
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        translation_key = translation_key_of(obj)
        if translation_key is not None:
            keys[translation_key] = name
    return keys


@pytest.mark.parametrize(
    "translation_file",
    TRANSLATION_FILES,
    ids=[path.stem for path in TRANSLATION_FILES],
)
def test_translation_files_have_exactly_en_keys(translation_file: Path) -> None:
    """Every translation file has exactly the key paths of en.json."""
    translation = load_translation(translation_file)

    expected = key_paths(EN)
    actual = key_paths(translation)

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    assert not missing and not extra, (
        f"{translation_file.name} does not match en.json: "
        f"missing={missing} extra={extra}"
    )


@pytest.mark.parametrize(
    ("platform", "module_name"),
    [("sensor", "sensor"), ("binary_sensor", "binary_sensor")],
)
def test_entity_translation_keys_exist(platform: str, module_name: str) -> None:
    """Every entity class has its translation key under entity.<platform>.<key>.name."""
    try:
        keys = class_translation_keys(module_name)
    except ImportError as err:  # binary_sensor.py is new
        pytest.fail(f"custom_components/bm6/{module_name}.py cannot be imported: {err}")

    assert keys, f"no entity classes with _attr_translation_key in {module_name}.py"

    missing = [
        f"{class_name} -> entity.{platform}.{key}.name"
        for key, class_name in sorted(keys.items())
        if not has_path(EN, f"entity.{platform}.{key}.name")
    ]
    assert not missing, f"missing translation keys: {missing}"


def enum_sensor_options() -> dict[str, list[str]]:
    """Return ``{translation_key: options}`` for every ENUM sensor class."""
    module = importlib.import_module("custom_components.bm6.sensor")
    coordinator = SimpleNamespace(
        data=None,
        device_address=ADDRESS,
        config_entry=SimpleNamespace(data=entry_data()),
        last_good_read=None,
    )
    options: dict[str, list[str]] = {}
    for _, entity_class in inspect.getmembers(module, inspect.isclass):
        if entity_class.__module__ != module.__name__:
            continue
        translation_key = translation_key_of(entity_class)
        if translation_key is None:
            continue
        entity = entity_class(coordinator)
        if entity.device_class is not SensorDeviceClass.ENUM:
            continue
        options[translation_key] = list(entity.options)
    return options


def test_enum_sensor_options_exist() -> None:
    """Every discovered ENUM sensor option exists under entity.sensor.<key>.state."""
    options = enum_sensor_options()
    assert options, "no ENUM sensors discovered in sensor.py"

    problems = []
    for translation_key, values in sorted(options.items()):
        assert values, f"ENUM sensor {translation_key} has no options"
        missing = [
            option
            for option in values
            if not has_path(EN, f"entity.sensor.{translation_key}.state.{option}")
        ]
        if missing:
            problems.append(f"entity.sensor.{translation_key}.state missing {missing}")
    assert not problems, "; ".join(problems)


def test_enum_sensor_options_match_spec() -> None:
    """The ENUM option lists are exactly the SPEC ones (no silent narrowing)."""
    options = enum_sensor_options()

    assert "state" in options and "device_state" in options
    assert set(options["device_state"]) == {"ok", "low_voltage", "charging"}
    assert len(options["device_state"]) == 3
    assert set(options["state"]) == {
        state.value for state in BatteryState if state is not BatteryState.Unknown
    }
    assert BatteryState.Unknown.value not in options["state"]


def test_battery_state_values_exist() -> None:
    """BatteryState values (except unknown) exist in the sensor and selector."""
    states = [
        state.value for state in BatteryState if state is not BatteryState.Unknown
    ]
    missing_sensor = [
        state for state in states if not has_path(EN, f"entity.sensor.state.state.{state}")
    ]
    missing_selector = [
        state
        for state in states
        if not has_path(EN, f"selector.battery_state.options.{state}")
    ]
    assert not missing_sensor, (
        f"entity.sensor.state.state is missing: {missing_sensor}"
    )
    assert not missing_selector, (
        f"selector.battery_state.options is missing: {missing_selector}"
    )


def test_selector_enum_values_exist() -> None:
    """Battery enums exist as options of their matching selector."""
    checks = {
        "selector.battery_state_algorithm.options": [
            value.value for value in BatteryStateAlgorithm
        ],
        "selector.battery_type.options": [value.value for value in BatteryType],
        "selector.battery_voltage.options": [
            value.value.lower() for value in BatteryVoltage
        ],
    }
    problems = []
    for path, values in checks.items():
        missing = [value for value in values if not has_path(EN, f"{path}.{value}")]
        if missing:
            problems.append(f"{path} is missing {missing}")
    assert not problems, "; ".join(problems)


def test_error_constants_exist() -> None:
    """Every ERROR_* constant of const.py exists under config.error."""
    errors = {
        name: value
        for name, value in vars(const).items()
        if name.startswith("ERROR_") and isinstance(value, str)
    }
    assert errors, "const.py defines no ERROR_* string constants"
    missing = [
        f"{name}={value}"
        for name, value in sorted(errors.items())
        if not has_path(EN, f"config.error.{value}")
    ]
    assert not missing, f"config.error is missing: {missing}"


def test_device_trigger_types_exist() -> None:
    """Every TRIGGER_TYPES key exists under device_automation.trigger_type."""
    missing = [
        trigger_type
        for trigger_type in device_trigger.TRIGGER_TYPES
        if not has_path(EN, f"device_automation.trigger_type.{trigger_type}")
    ]
    assert not missing, f"device_automation.trigger_type is missing: {missing}"


def test_new_entity_translations_exist() -> None:
    """The new last_update and reachable entities have English names."""
    missing = [
        path
        for path in (
            "entity.sensor.last_update.name",
            "entity.binary_sensor.reachable.name",
        )
        if not has_path(EN, path)
    ]
    assert not missing, f"en.json is missing: {missing}"
