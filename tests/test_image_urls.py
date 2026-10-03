"""Public model image policy and HA HTTP hook, with no real downloads."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.ninebot import adapters
from custom_components.ninebot.image import NinebotImage
from custom_components.ninebot.image_urls import public_image_url

HOST = "https://oms-oss-public.ninebot.com"


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "",
        "http://oms-oss-public.ninebot.com/a.png",
        "https://example.invalid/a.png",
        "https://oms-oss-public.ninebot.com.evil.invalid/a.png",
        "https://127.0.0.1/a.png",
        HOST + ":8443/a.png",
        "https://name:secret@oms-oss-public.ninebot.com/a.png",
        HOST + "/a.png#private",
        HOST + "/a.png?access_token=private",
        HOST + "/a.png?nbchecksignv1=a&account=b",
        HOST + "/a.png?nbchecksignv1=a&nbchecksignv1=b",
        HOST + "/a.png?bad",
        HOST + "/a.png?x=1&y=2&z=3",
        HOST + "/a.png?nbchecksignv1=hidden\nsecret",
        HOST + "//a.png",
        HOST + "/a/../b.png",
        HOST + "/%2e%2e/b.png",
        HOST + "/%00a.png",
        HOST + "/%xx.png",
        HOST + "/%FF.png",
        HOST + "/%5ca.png",
        HOST + "/a.svg",
        HOST + "/a.png" + "x" * 2100,
        "https://[invalid/a.png",
    ],
)
def test_unreviewed_or_unsafe_image_urls_are_not_used(value):
    assert public_image_url(value) is None


def test_observed_signature_is_removed_and_default_port_canonicalized():
    assert (
        public_image_url(HOST + ":443/model.png?nbchecksignv1=opaque-private")
        == HOST + "/model.png"
    )
    assert public_image_url(HOST + "/model.png") == HOST + "/model.png"
    assert public_image_url(HOST + "/image%20name.png") is None
    assert public_image_url(HOST + "\\model.png") is None
    assert public_image_url(HOST + "/model.png?nbchecksignv1=") == HOST + "/model.png"
    assert public_image_url("https://OMS-OSS-PUBLIC.NINEBOT.COM/model.png") == HOST + "/model.png"


def test_vehicle_profile_uses_only_public_asset_and_safe_fallback():
    data = json.loads(
        (Path(__file__).parent / "fixtures/ninecli/0.1.7/vehicle-image.json").read_text()
    )
    profile = adapters.profiles(data)[0]
    assert profile.image_url == HOST + "/synthetic-light.png"
    data[0]["v6_light_img_url"] = "https://unknown.invalid/private.png?token=secret"
    assert adapters.profiles(data)[0].image_url == HOST + "/synthetic-original.png"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_image_http_guard_rejects_redirects_and_sanitizes_failures(
    hass, entry, app_client, caplog
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity = NinebotImage(entry, "SyntheticSN")
    url = HOST + "/synthetic.png"
    response = httpx.Response(
        200,
        request=httpx.Request("GET", url),
        content=b"synthetic",
        headers={"content-type": "image/png"},
    )
    get = AsyncMock(return_value=response)
    with patch.object(entity._client, "get", get):
        entity._profile_image_url = url
        # Exercise Core's decoder/cache through our HTTP hook, not only a direct call.
        assert await entity.async_image() == b"synthetic"
        assert await entity.async_image() == b"synthetic"
        get.assert_awaited_once_with(url, timeout=10, follow_redirects=False)
        get.reset_mock()
        assert await entity._fetch_url(HOST + "/synthetic.png?nbchecksignv1=private") is None
        assert await entity._fetch_url("https://127.0.0.1/private.png") is None
        get.assert_not_awaited()
        for code in (302, 403, 204):
            get.return_value = httpx.Response(
                code,
                request=httpx.Request("GET", url),
                headers={"location": "https://127.0.0.1/private.png"},
            )
            assert await entity._fetch_url(url) is None
        get.side_effect = httpx.ConnectError(
            "private-token-must-not-be-logged", request=httpx.Request("GET", url)
        )
        assert await entity._fetch_url(url) is None
    assert "private-token-must-not-be-logged" not in caplog.text
    assert "opaque-private" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
