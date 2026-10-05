"""Release-level localization consistency, not a test of generated wording."""

import json
import re
from pathlib import Path

import pytest
from homeassistant.helpers.translation import async_get_translations

DIRECTORY = Path(__file__).parents[1] / "custom_components/ninebot"


def leaves(value, prefix=()):
    result = {}
    for key, child in value.items():
        if isinstance(child, dict):
            result.update(leaves(child, (*prefix, key)))
        else:
            result[(*prefix, key)] = child
    return result


def test_translation_resources_have_matching_structure_and_placeholders():
    english = json.loads((DIRECTORY / "translations/en.json").read_text())
    assert english == json.loads((DIRECTORY / "strings.json").read_text())
    base = leaves(english)
    locales = list((DIRECTORY / "translations").glob("*.json"))
    assert len(locales) >= 21
    for file in locales:
        content = json.loads(file.read_text())
        translated = leaves(content)
        assert translated.keys() == base.keys(), file.name
        for path, text in translated.items():
            assert isinstance(text, str) and text.strip(), (file.name, path)
            assert set(re.findall(r"\{([a-zA-Z0-9_]+)\}", text)) == set(
                re.findall(r"\{([a-zA-Z0-9_]+)\}", base[path])
            ), (file.name, path)
        for platform in content["entity"].values():
            for entity in platform.values():
                assert not re.search(r"\braw\b|原值|experimental", entity["name"], re.I)
    assert not any(key.startswith("estimated_") for key in english["entity"]["sensor"])


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "language,expected",
    [
        ("en", "Remaining range"),
        ("zh-Hans", "剩余续航"),
        ("de", "Restreichweite"),
        ("ja", "航続可能距離"),
    ],
)
async def test_real_ha_loads_localized_entity_resources(hass, language, expected):
    result = await async_get_translations(hass, language, "entity", {"ninebot"})
    assert result["component.ninebot.entity.sensor.endurance.name"] == expected
