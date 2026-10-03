"""Authentication borrowed from a Google Health config entry."""

from typing import cast

from aiohttp import ClientSession
from google_health_api.auth import AbstractAuth
from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session


class BorrowedEntryAuth(AbstractAuth):
    """Provide access tokens from the Google Health integration's OAuth session.

    The token lives in the Google Health config entry, so refreshes done here and
    by that integration land in the same place and neither side signs in twice.
    """

    def __init__(self, websession: ClientSession, oauth_session: OAuth2Session) -> None:
        """Initialize the auth helper."""
        super().__init__(websession)
        self._oauth_session = oauth_session

    async def async_get_access_token(self) -> str:
        """Return a valid access token."""
        await self._oauth_session.async_ensure_token_valid()
        return cast(str, self._oauth_session.token["access_token"])
