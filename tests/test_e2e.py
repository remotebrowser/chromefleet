import asyncio
import os
import sys

import httpx
import pytest
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from chromefleet import configure_remote_browser, kill_container, launch_container, settings

CHROMEFLEET_URL = os.getenv("CHROMEFLEET_URL", "http://localhost:8300")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=CHROMEFLEET_URL, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
async def async_client():
    async with httpx.AsyncClient(base_url=CHROMEFLEET_URL, timeout=30.0) as c:
        yield c


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert "OK" in response.text


class TestBrowserLifecycle:
    @pytest.fixture(autouse=True)
    def cleanup(self, client):
        self.browser_ids = []
        yield
        for browser_id in self.browser_ids:
            try:
                client.delete(f"/api/v1/browsers/{browser_id}")
            except Exception:
                pass

    def test_create_browser(self, client):
        response = client.post("/api/v1/browsers/test01")
        assert response.status_code == 200
        self.browser_ids.append("test01")
        data = response.json()
        assert data["status"] == "created"

    def test_get_browser(self, client):
        client.post("/api/v1/browsers/test02")
        self.browser_ids.append("test02")
        response = client.get("/api/v1/browsers/test02")
        assert response.status_code == 200
        data = response.json()
        assert "last_activity_timestamp" in data

    def test_get_nonexistent_browser(self, client):
        response = client.get("/api/v1/browsers/nonexistent-browser")
        assert response.status_code == 404

    def test_delete_browser(self, client):
        client.post("/api/v1/browsers/test03")
        self.browser_ids.append("test03")
        response = client.delete("/api/v1/browsers/test03")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        self.browser_ids.remove("test03")

    def test_delete_nonexistent_browser(self, client):
        response = client.delete("/api/v1/browsers/nonexistent-browser")
        assert response.status_code == 404


class TestBrowserListing:
    def test_list_browsers(self, client):
        response = client.get("/api/v1/browsers")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestProxyIp:
    # Uses 128.101.101.101 (University of Minnesota) which MaxMind resolves to US/MN.
    ORIGIN_IP = "128.101.101.101"

    @pytest.fixture(autouse=True)
    def cleanup(self):
        self.browser_ids = []
        yield
        for browser_id in self.browser_ids:
            try:
                asyncio.run(kill_container(f"chromium-{browser_id}"))
            except Exception:
                pass

    def test_ip_changes_with_origin_ip(self):
        browser_id = "test-proxy-ip"
        container_name = f"chromium-{browser_id}"
        self.browser_ids.append(browser_id)

        asyncio.run(launch_container(settings.CONTAINER_IMAGE, container_name))
        ip_after = asyncio.run(configure_remote_browser(browser_id, container_name, self.ORIGIN_IP))

        assert ip_after is not None, "Expected a public IP after proxy configuration"

        my_ip = httpx.get("https://ip.fly.dev", timeout=10).text.strip()
        assert ip_after != my_ip, f"Expected IP to change after proxy, but got {ip_after} (same as local {my_ip})"


class TestAutoStart:
    @pytest.fixture(autouse=True)
    def cleanup(self, client):
        self.browser_ids: list[str] = []
        yield
        for browser_id in self.browser_ids:
            try:
                client.delete(f"/api/v1/browsers/{browser_id}")
            except Exception:
                pass

    def test_cdp_websocket_autostart(self, client):
        browser_id = "test-autostart"
        self.browser_ids.append(browser_id)

        # Ensure the container does not already exist
        client.delete(f"/api/v1/browsers/{browser_id}")

        ws_base = CHROMEFLEET_URL.replace("http://", "ws://").replace("https://", "wss://")

        async def connect_and_verify():
            async with websockets.connect(f"{ws_base}/cdp/{browser_id}", open_timeout=60):
                pass  # successful connection confirms the container was auto-started

        asyncio.run(connect_and_verify())

        response = client.get(f"/api/v1/browsers/{browser_id}")
        assert response.status_code == 200
