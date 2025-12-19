# Chrome Fleet

**Requirements:** [Podman](https://podman.io), [uv](https://docs.astral.sh/uv), and [TS_AUTHKEY env](https://tailscale.com/kb/1085/auth-keys).

```bash
export TS_AUTHKEY=your-tailscale-auth-key
uv run chromefleet.py
```

For Dokku deployment, see the [deployment guide](deploy-dokku.md).

## API

### Start a new browser

`GET /api/v1/start/{id}` creates a new browser with the specified `id`. The browser runs in a container connected to Tailscale using `TS_AUTHKEY` and returns the Tailscale IP address and CDP URL.

_Example_: `curl localhost:8300/api/v1/start/xyz123` creates a container named `chromium-xyz123` (visible in the Tailscale admin console) and returns:

```json
{ "ip_address": "100.6.7.8", "cdp_url": "http://100.6.7.8:9222" }
```

### Stop a browser

`GET /api/v1/stop/{id}` terminates the browser with the specified `id` and returns the container name. Returns HTTP 404 if the browser ID is not found.

_Example_: `curl localhost:8300/api/v1/stop/xyz123` terminates the container named `chromium-xyz123` and returns `chromium-xyz123`.

### Query a browser

`GET /api/v1/query/{id}` returns information about the browser with the specified `id`, including its Tailscale IP address and CDP URL. Returns HTTP 404 if the browser is not found.

_Example_: `curl localhost:8300/api/v1/query/xyz123` returns:

```json
{ "ip_address": "100.6.7.8", "cdp_url": "http://100.6.7.8:9222" }
```

### List all browsers

`GET /api/v1/list` returns a JSON array of all running browser IDs.

_Example_: `curl localhost:8300/api/v1/list` returns:

```json
["xyz123", "abc234"]
```

### Connect via Tailscale

`GET /api/v1/connect/{id}` connects the container running the specified browser to Tailscale using `TS_AUTHKEY` and returns the Tailscale IP address and CDP URL.

This endpoint is useful when a container becomes disconnected from Tailscale. Note that new browsers are always started with an active Tailscale connection.

_Example_: `curl localhost:8300/api/v1/connect/xyz123` connects the browser via Tailscale as `chromium-xyz123` (visible in the Tailscale admin console) and returns:

```json
{ "ip_address": "100.6.7.8", "cdp_url": "http://100.6.7.8:9222" }
```

### Disconnect from Tailscale

`GET /api/v1/disconnect/{id}` disconnects the container running the specified browser from Tailscale.

_Example_: `curl localhost:8300/api/v1/disconnect/xyz123` disconnects browser `xyz123` (identified as `chromium-xyz123` in the Tailscale admin console).

### Configure a browser

`POST /api/v1/configure/{id}` configures the browser with the specified `id` using a JSON configuration body.

Currently supported configuration options include `proxy_url`, which sets an HTTP(S) proxy for the browser.

_Example_: `curl -X POST -H "Content-Type: application/json" -d '{"proxy_url": "http://proxy.example.com:8080"}' localhost:8300/api/v1/configure/xyz123` configures the proxy for browser `xyz123`.
