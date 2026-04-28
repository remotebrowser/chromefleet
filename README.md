# Chrome Fleet

**Requirements:** [Podman](https://podman.io) and [uv](https://docs.astral.sh/uv).

```bash
uv run src/chromefleet.py
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
