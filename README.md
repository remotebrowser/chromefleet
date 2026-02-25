# Chrome Fleet

**Requirements:** [Podman](https://podman.io) and [uv](https://docs.astral.sh/uv).

```bash
uv run chromefleet.py
```

For Dokku deployment, see the [deployment guide](deploy-dokku.md).

## API

### Start a new browser

`POST /api/v1/browsers/{browser_id}` creates a new browser with the specified `browser_id`. The browser runs in a container. 

_Example_: `curl -X POST localhost:8300/api/v1/browsers/xyz123` creates a container named `chromium-xyz123` and returns:

```json
{ "container_name": "chromium-xyz123", "status": "created" }
```

### Stop a browser

`DELETE /api/v1/browsers/{browser_id}` terminates the browser with the specified `browser_id` and returns the container name. Returns HTTP 404 if the browser ID is not found.

_Example_: `curl -X DELETE localhost:8300/api/v1/browsers/xyz123` terminates the container named `chromium-xyz123` and returns:

```json
{ "container_name": "chromium-xyz123", "status": "deleted" }
```

### Query a browser

`GET /api/v1/browsers/{browser_id}` returns information about the browser with the specified `browser_id`. Returns HTTP 404 if the browser is not found.

_Example_: `curl localhost:8300/api/v1/browsers/xyz123` returns:

```json
{ "last_activity_timestamp": 1772069081 }
```

### List all browsers

`GET /api/v1/browsers` returns a JSON array of all running browser IDs.

_Example_: `curl localhost:8300/api/v1/browsers` returns:

```json
["xyz123", "abc234"]
```

### Configure a browser

`POST /api/v1/browsers/{browser_id}/configure` configures the browser with the specified `browser_id` using a JSON configuration body.

Currently supported configuration options include `proxy_url`, which sets an HTTP(S) proxy for the browser.

_Example_: `curl -X POST -H "Content-Type: application/json" -d '{"proxy_url": "http://proxy.example.com:8080"}' localhost:8300/api/v1/browsers/xyz123/configure` configures the proxy for browser `xyz123` and returns:

```json
{ "status": "configured" }
```
