# Chrome Fleet

**Requirements:** [Podman](https://podman.io), [uv](https://docs.astral.sh/uv), and [TS_AUTHKEY env](https://tailscale.com/kb/1085/auth-keys).

```bash
export TS_AUTHKEY=your-tailscale-auth-key
uv run chromefleet.py
```

For Dokku deployment, see the [deployment guide](deploy-dokku.md).

## API

### Start a new browser

`POST /api/v1/browsers/{browser_id}` creates a new browser with the specified `browser_id`. The browser runs in a container connected to Tailscale using `TS_AUTHKEY` and returns the Tailscale IP address and CDP URL.

_Example_: `curl -X POST localhost:8300/api/v1/browsers/xyz123` creates a container named `chromium-xyz123` (visible in the Tailscale admin console) and returns:

```json
{ "ip_address": "100.6.7.8", "cdp_url": "http://100.6.7.8:9222" }
```

### Stop a browser

`DELETE /api/v1/browsers/{browser_id}` terminates the browser with the specified `browser_id` and returns the container name. Returns HTTP 404 if the browser ID is not found.

_Example_: `curl -X DELETE localhost:8300/api/v1/browsers/xyz123` terminates the container named `chromium-xyz123` and returns:

```json
{ "container_name": "chromium-xyz123", "status": "deleted" }
```

### Query a browser

`GET /api/v1/browsers/{browser_id}` returns information about the browser with the specified `browser_id`, including its Tailscale IP address and CDP URL. Returns HTTP 404 if the browser is not found.

_Example_: `curl localhost:8300/api/v1/browsers/xyz123` returns:

```json
{ "ip_address": "100.6.7.8", "cdp_url": "http://100.6.7.8:9222" }
```

### List all browsers

`GET /api/v1/browsers` returns a JSON array of all running browser IDs.

_Example_: `curl localhost:8300/api/v1/browsers` returns:

```json
["xyz123", "abc234"]
```

### Connect via Tailscale

`POST /api/v1/browsers/{browser_id}/connect` connects the container running the specified browser to Tailscale using `TS_AUTHKEY` and returns the Tailscale IP address and CDP URL.

This endpoint is useful when a container becomes disconnected from Tailscale. Note that new browsers are always started with an active Tailscale connection.

_Example_: `curl -X POST localhost:8300/api/v1/browsers/xyz123/connect` connects the browser via Tailscale as `chromium-xyz123` (visible in the Tailscale admin console) and returns:

```json
{ "ip_address": "100.6.7.8", "cdp_url": "http://100.6.7.8:9222" }
```

### Disconnect from Tailscale

`POST /api/v1/browsers/{browser_id}/disconnect` disconnects the container running the specified browser from Tailscale.

_Example_: `curl -X POST localhost:8300/api/v1/browsers/xyz123/disconnect` disconnects browser `xyz123` (identified as `chromium-xyz123` in the Tailscale admin console) and returns:

```json
{ "status": "disconnected" }
```

### Configure a browser

`POST /api/v1/browsers/{browser_id}/configure` configures the browser with the specified `browser_id` using a JSON configuration body.

Currently supported configuration options include `proxy_url`, which sets an HTTP(S) proxy for the browser.

_Example_: `curl -X POST -H "Content-Type: application/json" -d '{"proxy_url": "http://proxy.example.com:8080"}' localhost:8300/api/v1/browsers/xyz123/configure` configures the proxy for browser `xyz123` and returns:

```json
{ "status": "configured" }
```
