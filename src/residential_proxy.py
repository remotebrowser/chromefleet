from typing import Self

from pydantic import BaseModel, model_validator


class Location(BaseModel):
    """Location information for proxy configuration.

    Validation rules:
    - Country: Must be 2-char ISO code, normalized to lowercase
    - State: Normalized to lowercase with underscores, validated for US
    - Non-US countries: postal_code and state raise ValueError
    """

    country: str | None = None
    state: str | None = None
    city: str | None = None
    city_compacted: str | None = None
    postal_code: str | None = None

    @model_validator(mode="after")
    def validate_and_normalize(self) -> Self:
        self.country = (str(self.country) if self.country else "").lower().strip()
        self.state = (str(self.state) if self.state else "").lower().strip().replace(" ", "_")
        self.city = (str(self.city) if self.city else "").lower().strip().replace(" ", "_")
        self.postal_code = str(self.postal_code) if self.postal_code else None

        if not self.country or len(self.country) != 2 or not self.country.isalpha():
            raise ValueError(
                f"Invalid country code: '{self.country}'. Must be a 2-character ISO country code (e.g., 'us', 'uk')"
            )

        if self.country != "us":
            if self.postal_code:
                raise ValueError(f"postal_code not supported for non-US (country: '{self.country}')")
            if self.state:
                raise ValueError(f"state not supported for non-US (country: '{self.country}')")

        if self.city:
            self.city_compacted = self.city.lower().replace("-", "").replace("_", "").replace(" ", "")

        return self


def format_massive_proxy_url_from_location(
    location: Location,
    proxy_session_id: str,
    proxy_username: str,
    proxy_password: str,
) -> str:
    """
    Args:
        location: Location object with some of country, postal_code (for now, only these two are supported due to format differences with Oxylabs which the Location model is currently designed around)
        proxy_session_id: Session ID for proxy authentication
        proxy_password: Password for proxy authentication
        proxy_username: Username for proxy authentication
    Returns:
        Formatted proxy URL.
    """
    username_template = f"{proxy_username}"
    if location.country:
        username_template += f"-country-{location.country}"
    if (
        location.state and len(location.state) == 2
    ):  # only add state if it's a valid 2-letter code (currently only supporting US states)
        username_template += f"-subdivision-{location.state.upper()}"
    elif (
        location.postal_code
    ):  # don't want to unnecessarily constrain the pool size by adding postal code if state is already specified
        username_template += f"-zipcode-{location.postal_code}"
    # max ttl is 240 mins: https://docs.joinmassive.com/residential/sticky-sessions
    return f"http://{username_template}-session-{proxy_session_id}-sessionttl-240:{proxy_password}@network.joinmassive.com:65534"
