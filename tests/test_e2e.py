import os
import time

import httpx
import pytest

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


class TestBrowserConfiguration:
    @pytest.fixture(autouse=True)
    def cleanup(self, client):
        self.browser_ids = []
        yield
        for browser_id in self.browser_ids:
            try:
                client.delete(f"/api/v1/browsers/{browser_id}")
            except Exception:
                pass

    def test_configure_browser(self, client):
        client.post("/api/v1/browsers/test06")
        self.browser_ids.append("test06")
        for _ in range(10):
            response = client.get("/api/v1/browsers/test06")
            if response.status_code == 200:
                print(f"Browser ready: {response.json()}")
                break
            print(f"Browser not ready (status {response.status_code}), waiting...")
            time.sleep(3)
        response = client.post(
            "/api/v1/browsers/test06/configure",
            json={"proxy_url": "http://proxy.example.com:8080"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "configured"

    def test_configure_nonexistent_browser(self, client):
        response = client.post(
            "/api/v1/browsers/nonexistent-browser/configure",
            json={"proxy_url": "http://proxy.example.com:8080"},
        )
        assert response.status_code == 404
