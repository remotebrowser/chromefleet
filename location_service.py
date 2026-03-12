"""MaxMind GeoIP2 location service for resolving IP addresses to location data."""

from __future__ import annotations

from typing import Any, TypedDict, cast

import geoip2.webservice


class LocationData(TypedDict, total=False):
    ip: str
    longitude: float | None
    latitude: float | None
    city: str | None
    state: str | None
    country: str | None
    postal_code: str | None
    timezone: str | None


_ip_cache: dict[str, LocationData] = {}

_SKIP_IPS = {"unknown", "127.0.0.1", "localhost", "::1"}


def get_location_by_ip(
    ip_address: str,
    account_id: str,
    license_key: str,
) -> LocationData | None:
    """Resolve an IP address to location data using MaxMind GeoIP2 City API.

    Results are cached in memory by IP address.

    Returns None for local/unknown IPs or if the lookup fails.
    """
    if not ip_address or ip_address in _SKIP_IPS or "127.0.0.1" in ip_address:
        return None

    cached = _ip_cache.get(ip_address)
    if cached is not None:
        return cached

    if not account_id or not license_key:
        print("[LocationService] MaxMind account ID or license key not configured")
        return None

    try:
        client = geoip2.webservice.Client(int(account_id), license_key)
        response = client.city(ip_address)

        subdivisions = response.subdivisions
        state_name: str | None = None
        if subdivisions:
            last_sub = cast(Any, subdivisions[-1])
            names: dict[str, str] | None = getattr(last_sub, "names", None)
            state_name = names.get("en") if names else None

        location_data = LocationData(
            ip=ip_address,
            longitude=response.location.longitude if response.location else None,
            latitude=response.location.latitude if response.location else None,
            city=response.city.names.get("en") if response.city and response.city.names else None,
            state=state_name,
            country=response.country.iso_code if response.country else None,
            postal_code=response.postal.code if response.postal else None,
            timezone=response.location.time_zone if response.location else None,
        )

        _ip_cache[ip_address] = location_data
        print(f"[LocationService] Resolved IP {ip_address} -> {location_data}")
        return location_data

    except Exception as e:
        print(f"[LocationService] Error geolocating IP {ip_address}: {e}")
        return None
