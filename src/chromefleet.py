#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import subprocess
import sys

# Logfire registers a pydantic plugin via entry points that calls inspect.getsource()
# at import time, which fails in a PyInstaller frozen binary. Disable pydantic plugins
# when frozen so pydantic skips the entry point entirely.
if getattr(sys, "frozen", False):
    os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")
import urllib.request
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from loguru import Record

import logfire
import uvicorn
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict
from residential_proxy import Location, format_massive_proxy_url_from_location
from rich.logging import RichHandler
from websockets.exceptions import ConnectionClosed


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    CONTAINER_IMAGE: str = "ghcr.io/remotebrowser/chromium-live"
    MASSIVE_PROXY_USERNAME: str = ""
    MASSIVE_PROXY_PASSWORD: str = ""
    CONTAINER_HOST: str = ""
    GIT_REV: str = ""
    PORT: int = 8300
    ENV: str = "development"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    LOGFIRE_TOKEN: str = ""

    @property
    def MASSIVE_PROXY_ENABLED(self) -> bool:
        return bool(self.MASSIVE_PROXY_USERNAME and self.MASSIVE_PROXY_PASSWORD)


settings = Settings()


def setup_logging() -> None:
    rich_handler = RichHandler(rich_tracebacks=True, log_time_format="%X", markup=True)

    def _format_with_extra(record: "Record") -> str:
        message = record["message"]
        if record["extra"]:
            extra = yaml.dump(record["extra"], sort_keys=False, default_flow_style=False)
            message = f"{message}\n{extra}"
        return message.replace("[", r"\[").replace("{", "{{").replace("}", "}}").replace("<", r"\<")

    handlers: list[Any] = [
        {
            "sink": rich_handler,
            "format": _format_with_extra,
            "level": settings.LOG_LEVEL,
            "backtrace": True,
            "diagnose": True,
        }
    ]

    if settings.LOGFIRE_TOKEN:
        logfire.configure(
            service_name="chromefleet",
            send_to_logfire="if-token-present",
            token=settings.LOGFIRE_TOKEN,
            environment=settings.ENVIRONMENT,
            distributed_tracing=True,
            console=False,
            scrubbing=False,
        )
        logfire_handler = logfire.loguru_handler()
        logfire_handler["level"] = settings.LOG_LEVEL
        handlers.append(logfire_handler)

    logger.configure(handlers=handlers)

    if settings.LOGFIRE_TOKEN:
        logger.info("Logfire initialized")

    for lib_logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lib_logger = logging.getLogger(lib_logger_name)
        lib_logger.setLevel(settings.LOG_LEVEL)
        lib_logger.handlers.clear()
        lib_logger.addHandler(rich_handler)
        lib_logger.propagate = False


setup_logging()

DOCKER_INTERNAL_HOST = "172.17.0.1"


def run_podman(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = ["podman"]
    if settings.CONTAINER_HOST:
        cmd.append("--remote")
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")


async def get_host_port(container_name: str, container_port: int) -> int | None:
    try:
        result = run_podman(["port", container_name, str(container_port)])
        port_mapping = result.stdout.strip()
        if not port_mapping:
            return None
        host_port = int(port_mapping.split(":")[-1])
        return host_port
    except subprocess.CalledProcessError:
        return None


async def launch_container(image_name: str, container_name: str) -> str:
    logger.info(f"Launching Chromium container as {container_name}...")
    cmd = [
        "run",
        "-d",
        "--rm",
        "--name",
        container_name,
    ]
    # On macOS, Podman runs in a VM. This specific container image requires --privileged
    # to correctly access system services (like DBus) and devices inside that VM.
    if sys.platform == "darwin":
        cmd.append("--privileged")

    cmd.extend(
        [
            "-p",
            "9222",
            "-p",
            "5900",
            image_name,
        ]
    )
    try:
        result = run_podman(cmd)
        if result.returncode == 0 and result.stdout:
            container_id = result.stdout.strip()
            cdp_port = await get_host_port(container_name, 9222)
            vnc_port = await get_host_port(container_name, 5900)
            logger.info(
                f"Container started: name={container_name} id={container_id} cdp_port={cdp_port} vnc_port={vnc_port}"
            )
            return container_id
        raise Exception(f"Unable to launch Chromium for {container_name}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Unable to launch Chromium for {container_name}: {e}")


async def container_exists(container_name: str) -> bool:
    try:
        result = run_podman(["container", "exists", container_name])
        return result.returncode == 0
    except subprocess.CalledProcessError:
        return False


async def kill_container(container_name: str):
    logger.info(f"Killing Chromium container {container_name}...")
    try:
        result = run_podman(["kill", container_name])
        if result.returncode == 0 and result.stdout:
            logger.info(f"Container killed: name={container_name}")
        else:
            raise Exception(f"Unable to kill container {container_name}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Unable to kill container {container_name}: {e}")


async def list_containers() -> list[str]:
    logger.debug("Retrieving the list of all containers...")
    try:
        result = run_podman(["container", "ls", "--format", "{{.Names}}"])
        if result.returncode == 0:
            containers = result.stdout.splitlines() if result.stdout else []
            logger.debug(f"All containers obtained. Total={len(containers)}")
            return containers
        else:
            raise Exception("Unable to list all containers")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Unable to list all containers: {e}")


async def get_container_last_activity(container_name: str) -> float | None:
    try:
        run_podman(["exec", container_name, "sh", "-c", "cp $HOME/chrome-profile/Default/History db"])

        result = run_podman(["exec", container_name, "sqlite3", "db", "select MAX(last_visit_time) from urls;"])

        if result.returncode == 0 and result.stdout:
            chromium_time = float(result.stdout.strip())
            unix_epoch = (chromium_time / 1_000_000) - 11644473600
            return unix_epoch
        return None
    except subprocess.CalledProcessError:
        return None
    except Exception:
        return None


async def configure_container(container_name: str, config: dict[str, Any]) -> None:
    logger.info(f"Configuring container {container_name} with config {config}...")

    if proxy_url := config.get("proxy_url", ""):
        try:
            proxy_url = proxy_url.removeprefix("http://")
            logger.debug(f"Configuring proxy with proxy_url: {proxy_url}")
            logger.info(f"Modifying tinyproxy.conf in {container_name}...")
            run_podman(
                [
                    "exec",
                    container_name,
                    "sed",
                    "-i",
                    "/^Upstream http/d",
                    "/app/tinyproxy.conf",
                ]
            )
            run_podman(
                [
                    "exec",
                    container_name,
                    "sed",
                    "-i",
                    f"$ a\\Upstream http {proxy_url}",
                    "/app/tinyproxy.conf",
                ]
            )
            logger.info(f"Restarting tinyproxy in {container_name}...")
            run_podman(
                [
                    "exec",
                    container_name,
                    "sh",
                    "-c",
                    "pkill tinyproxy || true",
                ]
            )
            run_podman(
                [
                    "exec",
                    container_name,
                    "sh",
                    "-c",
                    "tinyproxy -d -c /app/tinyproxy.conf &",
                ]
            )
            logger.info(f"Proxy configured successfully in {container_name}.")
        except subprocess.CalledProcessError as e:
            raise Exception(f"Error configuring proxy: {e}")
        except Exception as e:
            logger.error(f"Error configuring proxy: {e}")


def get_git_revision() -> str:
    """Get the current git commit hash."""
    if settings.GIT_REV:
        return settings.GIT_REV
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


app = FastAPI(title="Chrome Fleet")
if settings.LOGFIRE_TOKEN:
    logfire.instrument_fastapi(app, capture_headers=True, excluded_urls="/health")


@app.get("/health")
async def health() -> str:
    git_rev = get_git_revision()[:10]
    return f"OK {int(datetime.now().timestamp())} GIT_REV: {git_rev}"


@app.post("/api/v1/browsers/{browser_id}")
async def create_browser(browser_id: str):
    logger.info(f"Starting browser {browser_id}...")
    container_name = f"chromium-{browser_id}"
    try:
        await launch_container(settings.CONTAINER_IMAGE, container_name)
        logger.info(f"Browser {browser_id} is started.")
        return {"container_name": container_name, "status": "created"}
    except Exception as e:
        detail = f"Unable to start browser {browser_id}!"
        logger.error(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.delete("/api/v1/browsers/{browser_id}")
async def delete_browser(browser_id: str):
    logger.info(f"Stopping browser {browser_id}...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        logger.warning(detail)
        raise HTTPException(status_code=404, detail=detail)
    try:
        await kill_container(container_name)
        logger.info(f"Browser {browser_id} is stopped.")
        return {"container_name": container_name, "status": "deleted"}
    except Exception as e:
        detail = f"Unable to stop browser {browser_id}!"
        logger.error(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.get("/api/v1/browsers/{browser_id}")
async def get_browser(browser_id: str):
    logger.info(f"Querying browser {browser_id}...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        logger.warning(detail)
        raise HTTPException(status_code=404, detail=detail)
    last_activity_timestamp = await get_container_last_activity(container_name)
    logger.debug(f"Browser {browser_id}: last_activity_timestamp={last_activity_timestamp}.")
    return {"last_activity_timestamp": last_activity_timestamp}


@app.get("/api/v1/browsers")
async def list_browsers():
    logger.info("Enumerating all browsers...")
    try:
        containers = await list_containers()
        all_browsers = [c[len("chromium-") :] for c in containers if c.startswith("chromium-")]
        return JSONResponse(all_browsers)
    except Exception as e:
        detail = "Unable to list all browsers"
        logger.error(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.post("/api/v1/browsers/{browser_id}/configure")
async def configure_browser(browser_id: str, config: dict[str, Any]):
    logger.info(f"Configuring browser {browser_id} with config {config}...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        logger.warning(detail)
        raise HTTPException(status_code=404, detail=detail)
    try:
        if settings.MASSIVE_PROXY_ENABLED:
            location = Location(**config.get("location", {}))
            if location:
                massive_url = format_massive_proxy_url_from_location(
                    location,
                    proxy_session_id=browser_id,
                    proxy_username=settings.MASSIVE_PROXY_USERNAME,
                    proxy_password=settings.MASSIVE_PROXY_PASSWORD,
                )
                logger.debug(f"Generated MassiveProxy proxy_url for browser {browser_id}: {massive_url}")
                config["proxy_url"] = massive_url

        await configure_container(container_name, config)
        logger.info(f"Browser {browser_id} is configured.")
        return {"status": "configured"}
    except Exception as e:
        detail = f"Unable to configure browser {browser_id}!"
        logger.error(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.get("/api/v1/suspend/{browser_id}")
async def suspend_browser(browser_id: str):
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/api/v1/resume/{browser_id}")
async def resume_browser(browser_id: str):
    raise HTTPException(status_code=501, detail="Not implemented")


def _container_host() -> str:
    return DOCKER_INTERNAL_HOST if os.path.exists("/.dockerenv") else "127.0.0.1"


async def get_cdp_url(browser_id: str) -> str:
    container_name = f"chromium-{browser_id}"
    host_port = await get_host_port(container_name, 9222)
    if not host_port:
        raise Exception(f"CDP port not found for {container_name}")
    return f"http://{_container_host()}:{host_port}"


async def get_cdp_websocket_url(browser_id: str) -> str:
    cdp_url = await get_cdp_url(browser_id)

    def fetch():
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=10) as response:
            data = json.loads(response.read().decode())
            logger.debug(f"[CDP] CDP json version gives {data}")
            return data["webSocketDebuggerUrl"]

    return await asyncio.to_thread(fetch)


async def get_page_websocket_url(browser_id: str, page_id: str) -> str | None:
    try:
        cdp_url = await get_cdp_url(browser_id)

        def fetch():
            with urllib.request.urlopen(f"{cdp_url}/json/list", timeout=10) as response:
                data = json.loads(response.read().decode())
                for item in data:
                    if item.get("id") == page_id:
                        return item.get("webSocketDebuggerUrl")
                return None

        return await asyncio.to_thread(fetch)
    except Exception as e:
        logger.error(f"[CDP] Error getting page websocket URL for {browser_id}/{page_id}: {e}")
        return None


async def get_page_list(browser_id: str) -> list[str]:
    try:
        cdp_url = await get_cdp_url(browser_id)

        def fetch():
            with urllib.request.urlopen(f"{cdp_url}/json/list", timeout=10) as response:
                data = json.loads(response.read().decode())
                return [item["id"] for item in data]

        return await asyncio.to_thread(fetch)
    except Exception as e:
        logger.error(f"[CDP] Error getting page list for {browser_id}: {e}")
        return []


async def find_browser_id(page_id: str) -> str | None:
    containers = await list_containers()
    for container in [c for c in containers if c.startswith("chromium-")]:
        browser_id = container.replace("chromium-", "")
        page_ids = await get_page_list(browser_id)
        if page_id in page_ids:
            return browser_id

    return None


def patch_cdp_target(message: str, browser_id: str) -> str:
    try:
        data = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return message

    if isinstance(data, dict):
        if data.get("method") == "Target.targetCreated":  # pyright: ignore[reportUnknownMemberType]
            params = data.get("params")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(params, dict):
                target_info = params.get("targetInfo")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
                if isinstance(target_info, dict) and "targetId" in target_info:
                    target_info["targetId"] = browser_id + "@" + str(target_info["targetId"])  # pyright: ignore[reportUnknownArgumentType]
                    return json.dumps(data)
        elif data.get("method") == "Target.getTargetInfo":  # pyright: ignore[reportUnknownMemberType]
            params = data.get("params")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(params, dict) and "targetId" in params:
                target_id = str(params["targetId"])  # pyright: ignore[reportUnknownArgumentType]
                if "@" in target_id:
                    params["targetId"] = target_id.split("@", 1)[1]
                    return json.dumps(data)
        elif "result" in data:
            result = data.get("result")  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            if isinstance(result, dict) and "targetId" in result:
                result["targetId"] = browser_id + "@" + str(result["targetId"])  # pyright: ignore[reportUnknownArgumentType]
                return json.dumps(data)

    return message


async def websocket_proxy(client_ws: WebSocket, remote_url: str, browser_id: str):
    try:
        async with websockets.connect(
            remote_url, ping_interval=60, ping_timeout=30, close_timeout=7200, max_size=10 * 1024 * 1024
        ) as remote_ws:
            logger.info("[CDP] Connected to remote WebSocket")

            async def client_to_remote():
                try:
                    while True:
                        message = await client_ws.receive_text()
                        message = patch_cdp_target(message, browser_id)
                        logger.debug(f"[CDP] Client -> Remote: {message[:100]}")
                        await remote_ws.send(message)
                except (WebSocketDisconnect, RuntimeError):
                    logger.info("[CDP] Client disconnected")
                except Exception as e:
                    logger.error(f"[CDP] client_to_remote error: {type(e).__name__}: {e}")

            async def remote_to_client():
                try:
                    async for message in remote_ws:
                        msg_text = message if isinstance(message, str) else message.decode()
                        msg_text = patch_cdp_target(msg_text, browser_id)
                        logger.debug(f"[CDP] Remote -> Client: {msg_text[:100]}")
                        if client_ws.client_state == WebSocketState.CONNECTED:
                            await client_ws.send_text(msg_text)
                        else:
                            logger.debug("[CDP] Client not connected, breaking")
                            break
                except ConnectionClosed as e:
                    logger.info(f"[CDP] Remote disconnected: code={e.code} reason={e.reason}")
                except Exception as e:
                    logger.error(f"[CDP] remote_to_client error: {type(e).__name__}: {e}")

            tasks = [
                asyncio.create_task(client_to_remote()),
                asyncio.create_task(remote_to_client()),
            ]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

            for task in pending:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    except OSError as e:
        logger.error(f"[CDP] Could not connect to remote: {e}")
        if client_ws.client_state == WebSocketState.CONNECTED:
            await client_ws.close(code=4502, reason="Remote server unreachable")
    except Exception as e:
        logger.error(f"[CDP] Unexpected error: {type(e).__name__}: {e}")
        if client_ws.client_state == WebSocketState.CONNECTED:
            await client_ws.close(code=4500, reason="Internal proxy error")


@app.websocket("/cdp/{browser_id}")
async def cdp_browser_websocket_proxy(client_ws: WebSocket, browser_id: str):
    logger.debug(f"[CDP] Entered cdp_browser_websocket_proxy for browser_id={browser_id}")
    container_name = f"chromium-{browser_id}"

    await client_ws.accept()
    logger.debug("[CDP] WebSocket accepted")

    if not await container_exists(container_name):
        logger.error(f"[CDP] Container {container_name} not found")
        await client_ws.close(code=1008)
        return

    remote_url = None
    for attempt in range(10):
        try:
            remote_url = await get_cdp_websocket_url(browser_id)
            logger.info(f"[CDP] Got remote URL: {remote_url}")
            break
        except Exception as e:
            logger.warning(f"[CDP] Attempt {attempt + 1}/10 failed to get debugger URL from {browser_id}: {e}")
            if attempt < 9:
                logger.debug("[CDP] Retrying in 3 seconds...")
                await asyncio.sleep(3)
            else:
                logger.error("[CDP] All retry attempts exhausted")
                await client_ws.close(code=4502, reason="Failed to get debugger URL")
                return

    if not remote_url:
        logger.error("[CDP] No remote URL obtained")
        await client_ws.close(code=4502, reason="Failed to get debugger URL")
        return

    logger.info(f"[CDP] Client connected, proxying to {remote_url}")
    await websocket_proxy(client_ws, remote_url, browser_id)
    logger.debug("[CDP] cdp_browser_websocket_proxy exiting")


@app.websocket("/devtools/{path:path}")
async def cdp_devtools_websocket_proxy(client_ws: WebSocket, path: str):
    logger.debug(f"[CDP] Entered cdp_devtools_websocket_proxy for path={path}")
    await client_ws.accept()
    logger.debug("[CDP] WebSocket accepted")

    parts = path.split("/")
    page_id = parts[-1] if parts else None
    if not page_id:
        logger.error("[CDP] No page_id in path")
        await client_ws.close(code=4000, reason="No page_id in path")
        return

    browser_id = None
    if "@" in page_id:
        parts = page_id.split("@")
        browser_id = parts[0]
        page_id = parts[1]
        logger.debug(f"[CDP] browser_id={browser_id} page_id={page_id}")
    else:
        logger.debug(f"[CDP] Looking for page_id={page_id}")
        browser_id = await find_browser_id(page_id)
        if browser_id:
            logger.debug(f"[CDP] Found page {page_id} in browser {browser_id}")
        else:
            logger.error(f"[CDP] Page {page_id} not found in any browser")
            await client_ws.close(code=4000, reason="Page not found in any browser")
            return

    remote_url = await get_page_websocket_url(browser_id, page_id)
    if not remote_url:
        logger.error(f"[CDP] Could not get websocket URL for page {page_id}")
        await client_ws.close(code=4502, reason="Failed to get page websocket URL")
        return

    logger.info(f"[CDP] Connecting to {remote_url}")
    await websocket_proxy(client_ws, remote_url, browser_id)
    logger.debug("[CDP] cdp_devtools_websocket_proxy exiting")


@app.get("/live/{browser_id}")
async def vnc_live_viewer(browser_id: str, request: Request):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{browser_id} - Live View</title>
        <style>
            body {{ margin: 0; background: #000; }}
            #screen {{ width: 100vw; height: 100vh; }}
        </style>
    </head>
    <body>
        <div id="screen"></div>
        <script type="module">
            import RFB from '/rfb.bundle.js';

            const wsScheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
            const wsUrl = wsScheme + '://' + window.location.host + '/websockify/{browser_id}';

            const rfb = new RFB(
                document.getElementById('screen'),
                wsUrl
            );
            rfb.scaleViewport = true;
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.websocket("/websockify/{browser_id}")
async def websockify_proxy(websocket: WebSocket, browser_id: str):
    container_name = f"chromium-{browser_id}"
    vnc_port = await get_host_port(container_name, 5900)
    if not vnc_port:
        await websocket.close()
        return

    client_subprotocol = websocket.headers.get("sec-websocket-protocol")
    if client_subprotocol and "binary" in [p.strip() for p in client_subprotocol.split(",")]:
        await websocket.accept(subprotocol="binary")
    else:
        await websocket.accept()

    try:
        reader, writer = await asyncio.open_connection(_container_host(), vnc_port)
    except Exception:
        await websocket.close()
        return

    async def ws_to_vnc():
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except Exception:
            writer.close()

    async def vnc_to_ws():
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass

    await asyncio.gather(ws_to_vnc(), vnc_to_ws())


_base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app.mount("/", StaticFiles(directory=os.path.join(_base_dir, "webui"), html=True), name="webui")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8300))
    frozen = getattr(sys, "frozen", False)  # set by PyInstaller
    uvicorn.run(app if frozen else "chromefleet:app", host="127.0.0.1", port=port, reload=not frozen)
