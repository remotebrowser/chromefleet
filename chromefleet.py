#!/usr/bin/env python3

import asyncio
import os
import subprocess
import sys
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

TS_AUTHKEY = os.getenv("TS_AUTHKEY")
CONTAINER_IMAGE = os.getenv("CONTAINER_IMAGE", "ghcr.io/remotebrowser/chromium-live")


def run_podman(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = ["podman"]
    if os.environ.get("CONTAINER_HOST"):
        cmd.append("--remote")
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")


async def get_tailscale_ip(container_name: str) -> str:
    try:
        result = run_podman(["exec", container_name, "sh", "-c", "tailscale ip"])
        if result.returncode == 0 and result.stdout:
            ip_address = result.stdout.strip().split("\n")[0]
            return ip_address
        raise Exception(f"Unable to get Tailscale IP address for {container_name}")
    except subprocess.CalledProcessError as e:
        raise Exception(f"Unable to get Tailscale IP address for {container_name}: {e}")


async def setup_tailscale(container_name: str) -> bool:
    try:
        cmd = f"sudo tailscale up --auth-key={TS_AUTHKEY} --hostname={container_name} --advertise-tags=tag:chromefleet"
        result = run_podman(["exec", container_name, "sh", "-c", cmd])
        if result.returncode != 0:
            print(f"Failed to execute tailscale up: {result.stderr}")
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"Failed to execute tailscale up: {e.stderr}")
        return False
    except Exception as e:
        print(f"Error setting up Tailscale: {e}")
        return False


async def terminate_tailscale(container_name: str) -> bool:
    try:
        cmd = "sudo tailscale logout"
        result = run_podman(["exec", container_name, "sh", "-c", cmd])
        if result.returncode != 0:
            print(f"Failed to execute tailscale logout: {result.stderr}")
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"Failed to execute tailscale logout: {e.stderr}")
        return False
    except Exception as e:
        print(f"Error terminating Tailscale: {e}")
        return False


async def launch_container(image_name: str, container_name: str) -> str:
    print(f"Launching Chromium container as {container_name}...")
    cmd = [
        "run",
        "-d",
        "--rm",
        "--name",
        container_name,
        "--cap-add=NET_ADMIN",
        "--cap-add=NET_RAW",
        "--device",
        "/dev/net/tun:/dev/net/tun",
        image_name,
    ]
    try:
        result = run_podman(cmd)
        if result.returncode == 0 and result.stdout:
            container_id = result.stdout.strip()
            print(f"Container started: name={container_name} id={container_id}")
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


async def configure_container(container_name: str, config: dict[str, str]) -> None:
    ip = await get_tailscale_ip(container_name)
    print(f"Configuring container {container_name} with IP {ip} and config {config}...")

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
        return await connect_browser_to_tailscale(browser_id)
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
    MAX_ATTEMPTS = 3
    for attempt in range(MAX_ATTEMPTS):
        print(f"Getting information for {container_name}: attempt {attempt + 1}/{MAX_ATTEMPTS}...")
        try:
            ip_address = await get_tailscale_ip(container_name)
            cdp_url = f"http://{ip_address}:9222"
            last_activity_timestamp = await get_container_last_activity(container_name)
            print(f"Browser {browser_id}: ip_address={ip_address} cdp_url={cdp_url}.")
            return {"ip_address": ip_address, "cdp_url": cdp_url, "last_activity_timestamp": last_activity_timestamp}
        except Exception as e:
            await asyncio.sleep(1)
            if attempt + 1 == MAX_ATTEMPTS:
                detail = f"Unable to get Tailscale IP address for {container_name}!"
                print(f"{detail} Exception={e}")
                raise HTTPException(status_code=500, detail=detail)


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


async def connect_browser_to_tailscale(browser_id: str):
    print(f"Connecting browser {browser_id} to Tailscale...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        print(detail)
        raise HTTPException(status_code=404, detail=detail)
    try:
        MAX_ATTEMPTS = 3
        for attempt in range(MAX_ATTEMPTS):
            await asyncio.sleep(1)
            print(f"Setting up Tailscale for {container_name}: attempt {attempt + 1}/{MAX_ATTEMPTS}...")
            if await setup_tailscale(container_name):
                print(f"Browser {browser_id} is connected to Tailscale.")
                return await get_browser(browser_id)
        raise Exception(f"Unable to setup Tailscale after {MAX_ATTEMPTS}!")
    except Exception as e:
        detail = f"Unable to connect browser {browser_id} to Tailscale!"
        print(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.post("/api/v1/browsers/{browser_id}/connect")
async def connect_browser(browser_id: str):
    return await connect_browser_to_tailscale(browser_id)


@app.post("/api/v1/browsers/{browser_id}/disconnect")
async def disconnect_browser(browser_id: str):
    print(f"Disconnecting browser {browser_id} from Tailscale...")
    container_name = f"chromium-{browser_id}"
    if not await container_exists(container_name):
        detail = f"Browser {browser_id} not found!"
        print(detail)
        raise HTTPException(status_code=404, detail=detail)
    try:
        await terminate_tailscale(container_name)
        print(f"Browser {browser_id} is disconnected from Tailscale")
        return {"status": "disconnected"}
    except Exception as e:
        detail = f"Unable to disconnect browser {browser_id} from Tailscale!"
        print(f"{detail} Exception={e}")
        raise HTTPException(status_code=500, detail=detail)


@app.post("/api/v1/browsers/{browser_id}/configure")
async def configure_browser(browser_id: str, config: dict[str, str]):
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


app.mount("/", StaticFiles(directory="webui", html=True), name="webui")


if __name__ == "__main__":
    if not TS_AUTHKEY:
        print("Fatal error: TS_AUTHKEY env is not present", file=sys.stderr)
        sys.exit(1)
    port = int(os.getenv("PORT", 8300))
    uvicorn.run("chromefleet:app", host="0.0.0.0", port=port, reload=True)
