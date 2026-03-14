from geoip2.webservice import AsyncClient
from geoip2.errors import GeoIP2Error


_SKIP_IPS = {"127.0.0.1", "::1", "localhost", "unknown"}


async def get_location_by_ip(
    ip_address: str,
    account_id: int,
    license_key: str,
) -> dict[str, str | None] | None:
    """Look up geolocation for an IP via MaxMind GeoIP2 City web service.

    Returns a dict with keys: country, state, city, postal_code
    or None if the IP is local/unknown or lookup fails.
    """
    if not ip_address or ip_address in _SKIP_IPS:
        return None

    async with AsyncClient(account_id, license_key) as client:
        try:
            response = await client.city(ip_address)
        except GeoIP2Error:
            return None

    country = response.country.iso_code  # e.g. "US"
    subdivision = response.subdivisions.most_specific
    state = subdivision.iso_code  # e.g. "CA" (already 2-letter for US states)
    city = response.city.name
    postal_code = response.postal.code

    return {
        "country": country.lower() if country else None,
        "state": state.upper() if state else None,
        "city": city,
        "postal_code": postal_code,
    }
