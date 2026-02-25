#!/usr/bin/env python3

import asyncio
import json
import os
import subprocess
import sys
import urllib.request
import websockets
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

CONTAINER_IMAGE = os.getenv("CONTAINER_IMAGE", "ghcr.io/remotebrowser/chromium-live")


def run_podman(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = ["podman"]
    if os.environ.get("CONTAINER_HOST"):
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
    print(f"Launching Chromium container as {container_name}...")
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

    cmd.extend([
        "-p",
        "9222",
        "-p",
        "5900",
        image_name,
    ])
    try:
        result = run_podman(cmd)
        if result.returncode == 0 and result.stdout:
            container_id = result.stdout.strip()
            cdp_port = await get_host_port(container_name, 9222)
            vnc_port = await get_host_port(container_name, 5900)
            print(f"Container started: name={container_name} id={container_id} cdp_port={cdp_port} vnc_port={vnc_port}")
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
    print(f"Killing Chromium contaner {container_name}...")
    try:
        result = run_podman(["kill", container_name])
        if result.returncode == 0 and result.stdout:
            print(f"Container killed: name={container_name}")
        else:
            raise Exception(f"Unable to kill container {container_name}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Unable to kill container {container_name}: {e}")


async def list_containers() -> list[str]:
    print("Retrieving the list of all containers...")
    try:
        result = run_podman(["container", "ls", "--format", "{{.Names}}"])
        if result.returncode == 0:
            containers = result.stdout.splitlines() if result.stdout else []
            print(f"All containers are obtained. Total={len(containers)}")
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
    print(f"Configuring container {container_name} with config {config}...")

    if proxy_url := config.get("proxy_url", ""):
        try:
            print(f"Configuring proxy with proxy_url: {proxy_url}")
            print(f"Modifying tinyproxy.conf in {container_name}...")
            run_podman([
                "exec",
                container_name,
                "sed",
                "-i",
                "/^Upstream http/d",
                "/app/tinyproxy.conf",
            ])
            run_podman([
                "exec",
                container_name,
                "sed",
                "-i",
                f"$ a\\Upstream http {proxy_url}",
                "/app/tinyproxy.conf",
            ])
            print(f"Restarting tinyproxy in {container_name}...")
            run_podman([
                "exec",
                container_name,
                "pkill",
                "tinyproxy",
            ])
            run_podman([
                "exec",
                container_name,
                "sh",
                "-c",
                "tinyproxy -d -c /app/tinyproxy.conf &",
            ])
            print(f"Proxy configured successfully in {container_name}.")
        except subprocess.CalledProcessError as e:
            raise Exception(f"Error configuring proxy: {e}")
        except Exception as e:
            print(f"Error configuring proxy: {e}")


def get_git_revision() -> str:
    """Get the current git commit hash."""
    git_rev = os.getenv("GIT_REV")
    if git_rev:
        return git_rev
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


@app.get("/health")
async def health() -> str:
    git_rev = get_git_revision()[:10]
    return f"OK {int(datetime.now().timestamp())} GIT_REV: {git_rev}"


@app.post("/api/v1/browsers/{browser_id}")
async def create_browser(browser_id: str):
    print(f"Starting browser {browser_id}...")
    container_name = f"chromium-{browser_id}"
    try:
        await launch_container(CONTAINER_IMAGE, container_name)
        print(f"Browser {browser_id} is started.")
        return {"container_name": container_name, "status": "created"}
    except Exception as e:
        detail = f"Unable to start browser {browser_id}!"
        print(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.delete("/api/v1/browsers/{browser_id}")
async def delete_browser(browser_id: str):
    print(f"Stopping browser {browser_id}...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        print(detail)
        raise HTTPException(status_code=404, detail=detail)
    try:
        await kill_container(container_name)
        print(f"Browser {browser_id} is stopped.")
        return {"container_name": container_name, "status": "deleted"}
    except Exception as e:
        detail = f"Unable to stop browser {browser_id}!"
        print(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.get("/api/v1/browsers/{browser_id}")
async def get_browser(browser_id: str):
    print(f"Querying browser {browser_id}...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        print(detail)
        raise HTTPException(status_code=404, detail=detail)
    last_activity_timestamp = await get_container_last_activity(container_name)
    print(f"Browser {browser_id}: last_activity_timestamp={last_activity_timestamp}.")
    return {"last_activity_timestamp": last_activity_timestamp}


@app.get("/api/v1/browsers")
async def list_browsers():
    print("Enumerating all browsers...")
    try:
        containers = await list_containers()
        all_browsers = [c[len("chromium-") :] for c in containers if c.startswith("chromium-")]
        return JSONResponse(all_browsers)
    except Exception as e:
        detail = "Unable to list all browsers"
        print(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.post("/api/v1/browsers/{browser_id}/configure")
async def configure_browser(browser_id: str, config: dict[str, Any]):
    print(f"Configuring browser {browser_id} with config {config}...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        print(detail)
        raise HTTPException(status_code=404, detail=detail)
    try:
        await configure_container(container_name, config)
        print(f"Browser {browser_id} is configured.")
        return {"status": "configured"}
    except Exception as e:
        detail = f"Unable to configure browser {browser_id}!"
        print(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.get("/api/v1/suspend/{browser_id}")
async def suspend_browser(browser_id: str):
    raise HTTPException(status_code=501, detail="Not implemented")


@app.get("/api/v1/resume/{browser_id}")
async def resume_browser(browser_id: str):
    raise HTTPException(status_code=501, detail="Not implemented")


async def get_cdp_url(browser_id: str) -> str:
    container_name = f"chromium-{browser_id}"
    host_port = await get_host_port(container_name, 9222)
    if not host_port:
        raise Exception(f"CDP port not found for {container_name}")
    return f"http://127.0.0.1:{host_port}"


async def get_cdp_websocket_url(browser_id: str) -> str:
    cdp_url = await get_cdp_url(browser_id)

    def fetch():
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=10) as response:
            data = json.loads(response.read().decode())
            print(f"[CDP] CDP json version gives {data}")
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
        print(f"[CDP] Error getting page websocket URL for {browser_id}/{page_id}: {e}")
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
        print(f"[CDP] Error getting page list for {browser_id}: {e}")
        return []


async def find_browser_id(page_id: str) -> str | None:
    containers = await list_containers()
    for container in [c for c in containers if c.startswith("chromium-")]:
        browser_id = container.replace("chromium-", "")
        page_ids = await get_page_list(browser_id)
        if page_id in page_ids:
            return browser_id

    return None


async def websocket_proxy(client_ws: WebSocket, remote_url: str):
    try:
        async with websockets.connect(
            remote_url, ping_interval=60, ping_timeout=30, close_timeout=7200, max_size=10 * 1024 * 1024
        ) as remote_ws:
            print("[CDP] Connected to remote WebSocket")

            async def client_to_remote():
                try:
                    while True:
                        message = await client_ws.receive_text()
                        await remote_ws.send(message)
                except (WebSocketDisconnect, RuntimeError):
                    print("[CDP] Client disconnected")
                except Exception as e:
                    print(f"[CDP] client_to_remote error: {type(e).__name__}: {e}")

            async def remote_to_client():
                try:
                    async for message in remote_ws:
                        if client_ws.client_state == WebSocketState.CONNECTED:
                            await client_ws.send_text(message if isinstance(message, str) else message.decode())
                        else:
                            print("[CDP] Client not connected, breaking")
                            break
                except ConnectionClosed as e:
                    print(f"[CDP] Remote disconnected: code={e.code} reason={e.reason}")
                except Exception as e:
                    print(f"[CDP] remote_to_client error: {type(e).__name__}: {e}")

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
        print(f"[CDP] Could not connect to remote: {e}")
        if client_ws.client_state == WebSocketState.CONNECTED:
            await client_ws.close(code=4502, reason="Remote server unreachable")
    except Exception as e:
        print(f"[CDP] Unexpected error: {type(e).__name__}: {e}")
        if client_ws.client_state == WebSocketState.CONNECTED:
            await client_ws.close(code=4500, reason="Internal proxy error")


@app.websocket("/cdp/{browser_id}")
async def cdp_browser_websocket_proxy(client_ws: WebSocket, browser_id: str):
    print(f"[CDP] Entered cdp_browser_websocket_proxy for browser_id={browser_id}")
    container_name = f"chromium-{browser_id}"

    await client_ws.accept()
    print("[CDP] WebSocket accepted")

    if not await container_exists(container_name):
        print(f"[CDP] Container {container_name} not found")
        await client_ws.close(code=1008)
        return

    try:
        remote_url = await get_cdp_websocket_url(browser_id)
        print(f"[CDP] Got remote URL: {remote_url}")
    except Exception as e:
        print(f"[CDP] Failed to get debugger URL from {browser_id}: {e}")
        await client_ws.close(code=4502, reason="Failed to get debugger URL")
        return

    print(f"[CDP] Client connected, proxying to {remote_url}")
    await websocket_proxy(client_ws, remote_url)
    print("[CDP] cdp_browser_websocket_proxy exiting")


@app.websocket("/devtools/{path:path}")
async def cdp_devtools_websocket_proxy(client_ws: WebSocket, path: str):
    print(f"[CDP] Entered cdp_devtools_websocket_proxy for path={path}")
    await client_ws.accept()
    print("[CDP] WebSocket accepted")

    parts = path.split("/")
    page_id = parts[-1] if parts else None
    if not page_id:
        print("[CDP] No page_id in path")
        await client_ws.close(code=4000, reason="No page_id in path")
        return

    print(f"[CDP] Looking for page_id={page_id}")

    browser_id = await find_browser_id(page_id)
    if not browser_id:
        print(f"[CDP] Page {page_id} not found in any browser")
        await client_ws.close(code=4000, reason="Page not found in any browser")
        return

    print(f"[CDP] Found page {page_id} in browser {browser_id}")

    remote_url = await get_page_websocket_url(browser_id, page_id)
    if not remote_url:
        print(f"[CDP] Could not get websocket URL for page {page_id}")
        await client_ws.close(code=4502, reason="Failed to get page websocket URL")
        return

    print(f"[CDP] Connecting to {remote_url}")
    await websocket_proxy(client_ws, remote_url)
    print("[CDP] cdp_devtools_websocket_proxy exiting")


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

            const rfb = new RFB(
                document.getElementById('screen'),
                'ws://{request.headers["host"]}/websockify/{browser_id}'
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

    await websocket.accept(subprotocol="binary")

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", vnc_port)
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


app.mount("/", StaticFiles(directory="webui", html=True), name="webui")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8300))
    uvicorn.run("chromefleet:app", host="0.0.0.0", port=port, reload=True)
