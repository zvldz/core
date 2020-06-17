"""The tests for generic camera component."""
import asyncio

from homeassistant.components.websocket_api.const import TYPE_RESULT
from homeassistant.const import HTTP_INTERNAL_SERVER_ERROR, HTTP_NOT_FOUND
from homeassistant.setup import async_setup_component

from tests.async_mock import patch


async def test_fetching_url(aioclient_mock, hass, hass_client):
    """Test that it fetches the given url."""
    aioclient_mock.get("http://example.com", text="hello world")

    await async_setup_component(
        hass,
        "camera",
        {
            "camera": {
                "name": "config_test",
                "platform": "generic",
                "still_image_url": "http://example.com",
                "username": "user",
                "password": "pass",
            }
        },
    )

    client = await hass_client()

    resp = await client.get("/api/camera_proxy/camera.config_test")

    assert resp.status == 200
    assert aioclient_mock.call_count == 1
    body = await resp.text()
    assert body == "hello world"

    resp = await client.get("/api/camera_proxy/camera.config_test")
    assert aioclient_mock.call_count == 2


async def test_fetching_without_verify_ssl(aioclient_mock, hass, hass_client):
    """Test that it fetches the given url when ssl verify is off."""
    aioclient_mock.get("https://example.com", text="hello world")

    await async_setup_component(
        hass,
        "camera",
        {
            "camera": {
                "name": "config_test",
                "platform": "generic",
                "still_image_url": "https://example.com",
                "username": "user",
                "password": "pass",
                "verify_ssl": "false",
            }
        },
    )

    client = await hass_client()

    resp = await client.get("/api/camera_proxy/camera.config_test")

    assert resp.status == 200


async def test_fetching_url_with_verify_ssl(aioclient_mock, hass, hass_client):
    """Test that it fetches the given url when ssl verify is explicitly on."""
    aioclient_mock.get("https://example.com", text="hello world")

    await async_setup_component(
        hass,
        "camera",
        {
            "camera": {
                "name": "config_test",
                "platform": "generic",
                "still_image_url": "https://example.com",
                "username": "user",
                "password": "pass",
                "verify_ssl": "true",
            }
        },
    )

    client = await hass_client()

    resp = await client.get("/api/camera_proxy/camera.config_test")

    assert resp.status == 200


async def test_limit_refetch(aioclient_mock, hass, hass_client):
    """Test that it fetches the given url."""
    aioclient_mock.get("http://example.com/5a", text="hello world")
    aioclient_mock.get("http://example.com/10a", text="hello world")
    aioclient_mock.get("http://example.com/15a", text="hello planet")
    aioclient_mock.get("http://example.com/20a", status=HTTP_NOT_FOUND)

    await async_setup_component(
        hass,
        "camera",
        {
            "camera": {
                "name": "config_test",
                "platform": "generic",
                "still_image_url": 'http://example.com/{{ states.sensor.temp.state + "a" }}',
                "limit_refetch_to_url_change": True,
            }
        },
    )

    client = await hass_client()

    resp = await client.get("/api/camera_proxy/camera.config_test")

    hass.states.async_set("sensor.temp", "5")

    with patch("async_timeout.timeout", side_effect=asyncio.TimeoutError()):
        resp = await client.get("/api/camera_proxy/camera.config_test")
        assert aioclient_mock.call_count == 0
        assert resp.status == HTTP_INTERNAL_SERVER_ERROR

    hass.states.async_set("sensor.temp", "10")

    resp = await client.get("/api/camera_proxy/camera.config_test")
    assert aioclient_mock.call_count == 1
    assert resp.status == 200
    body = await resp.text()
    assert body == "hello world"

    resp = await client.get("/api/camera_proxy/camera.config_test")
    assert aioclient_mock.call_count == 1
    assert resp.status == 200
    body = await resp.text()
    assert body == "hello world"

    hass.states.async_set("sensor.temp", "15")

    # Url change = fetch new image
    resp = await client.get("/api/camera_proxy/camera.config_test")
    assert aioclient_mock.call_count == 2
    assert resp.status == 200
    body = await resp.text()
    assert body == "hello planet"

    # Cause a template render error
    hass.states.async_remove("sensor.temp")
    resp = await client.get("/api/camera_proxy/camera.config_test")
    assert aioclient_mock.call_count == 2
    assert resp.status == 200
    body = await resp.text()
    assert body == "hello planet"


async def test_stream_source(aioclient_mock, hass, hass_client, hass_ws_client):
    """Test that the stream source is rendered."""
    assert await async_setup_component(
        hass,
        "camera",
        {
            "camera": {
                "name": "config_test",
                "platform": "generic",
                "still_image_url": "https://example.com",
                "stream_source": 'http://example.com/{{ states.sensor.temp.state + "a" }}',
                "limit_refetch_to_url_change": True,
            }
        },
    )

    hass.states.async_set("sensor.temp", "5")

    with patch(
        "homeassistant.components.camera.request_stream",
        return_value="http://home.assistant/playlist.m3u8",
    ) as mock_request_stream:
        # Request playlist through WebSocket
        client = await hass_ws_client(hass)

        await client.send_json(
            {"id": 1, "type": "camera/stream", "entity_id": "camera.config_test"}
        )
        msg = await client.receive_json()

        # Assert WebSocket response
        assert mock_request_stream.call_count == 1
        assert mock_request_stream.call_args[0][1] == "http://example.com/5a"
        assert msg["id"] == 1
        assert msg["type"] == TYPE_RESULT
        assert msg["success"]
        assert msg["result"]["url"][-13:] == "playlist.m3u8"

        # Cause a template render error
        hass.states.async_remove("sensor.temp")

        await client.send_json(
            {"id": 2, "type": "camera/stream", "entity_id": "camera.config_test"}
        )
        msg = await client.receive_json()

        # Assert that no new call to the stream request should have been made
        assert mock_request_stream.call_count == 1
        # Assert the websocket error message
        assert msg["id"] == 2
        assert msg["type"] == TYPE_RESULT
        assert msg["success"] is False
        assert msg["error"] == {
            "code": "start_stream_failed",
            "message": "camera.config_test does not support play stream service",
        }


async def test_no_stream_source(aioclient_mock, hass, hass_client, hass_ws_client):
    """Test a stream request without stream source option set."""
    assert await async_setup_component(
        hass,
        "camera",
        {
            "camera": {
                "name": "config_test",
                "platform": "generic",
                "still_image_url": "https://example.com",
                "limit_refetch_to_url_change": True,
            }
        },
    )

    with patch(
        "homeassistant.components.camera.request_stream",
        return_value="http://home.assistant/playlist.m3u8",
    ) as mock_request_stream:
        # Request playlist through WebSocket
        client = await hass_ws_client(hass)

        await client.send_json(
            {"id": 3, "type": "camera/stream", "entity_id": "camera.config_test"}
        )
        msg = await client.receive_json()

        # Assert the websocket error message
        assert mock_request_stream.call_count == 0
        assert msg["id"] == 3
        assert msg["type"] == TYPE_RESULT
        assert msg["success"] is False
        assert msg["error"] == {
            "code": "start_stream_failed",
            "message": "camera.config_test does not support play stream service",
        }


async def test_camera_content_type(aioclient_mock, hass, hass_client):
    """Test generic camera with custom content_type."""
    svg_image = "<some image>"
    urlsvg = "https://upload.wikimedia.org/wikipedia/commons/0/02/SVG_logo.svg"
    aioclient_mock.get(urlsvg, text=svg_image)

    cam_config_svg = {
        "name": "config_test_svg",
        "platform": "generic",
        "still_image_url": urlsvg,
        "content_type": "image/svg+xml",
    }
    cam_config_normal = cam_config_svg.copy()
    cam_config_normal.pop("content_type")
    cam_config_normal["name"] = "config_test_jpg"

    await async_setup_component(
        hass, "camera", {"camera": [cam_config_svg, cam_config_normal]}
    )

    client = await hass_client()

    resp_1 = await client.get("/api/camera_proxy/camera.config_test_svg")
    assert aioclient_mock.call_count == 1
    assert resp_1.status == 200
    assert resp_1.content_type == "image/svg+xml"
    body = await resp_1.text()
    assert body == svg_image

    resp_2 = await client.get("/api/camera_proxy/camera.config_test_jpg")
    assert aioclient_mock.call_count == 2
    assert resp_2.status == 200
    assert resp_2.content_type == "image/jpeg"
    body = await resp_2.text()
    assert body == svg_image
