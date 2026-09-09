"""Login module."""

from typing import Any, Collection, Hashable, Optional, Tuple, Union

from synapse.api.ratelimiting import Ratelimiter
from synapse.config.ratelimiting import RatelimitSettings
from synapse.module_api import NOT_SPAM, ModuleApi, errors
from synapse.types import Requester

from connect.base_config import BaseConfig


class Config(BaseConfig):
    """Configuration for login module."""

    rc_login_global: dict[str, Any]


GLOBAL = "GLOBAL"


class GlobalRatelimiter(Ratelimiter):
    """A ratelimiter that applies across all hosts and users."""

    def _get_key(
        self, requester: Optional[Requester], key: Optional[Hashable]
    ) -> Hashable:
        return GLOBAL


class Module:
    """A module that handles login."""

    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        api.register_spam_checker_callbacks(
            check_login_for_spam=self.check_login_for_spam
        )
        hs = api._hs
        store = hs.get_datastores().main
        clock = hs.get_clock()

        key = "rc_login_global"
        per_second = config[key]["per_second"]
        burst_count = config[key]["burst_count"]
        cfg = RatelimitSettings(key, per_second, burst_count)

        self.login_ratelimiter = GlobalRatelimiter(store, clock, cfg)

    async def check_login_for_spam(
        self,
        user_id: str,
        device_id: Optional[str],
        initial_display_name: Optional[str],
        request_info: Collection[Tuple[Optional[str], str]],
        auth_provider_id: Optional[str] = None,
    ) -> Union[NOT_SPAM, errors.Codes]:
        # This method raises a LimitExceededError if there are too many
        # login attempts, across all users, within a given time.

        await self.login_ratelimiter.ratelimit(None)
        self._reset_action_count()
        return NOT_SPAM

    def _reset_action_count(self):
        # The built-in ratelimiting only allows N simultaneous requests one time
        # before users must wait M minutes for just one additional request.
        # This method enables N simultaneous requests every M minutes.

        limiter = self.login_ratelimiter
        action = limiter.actions.get(GLOBAL)
        if not action:
            return

        new_time_start = limiter.clock.time()
        _, time_start, rate_hz = action
        was_last_login_recent = rate_hz * (new_time_start - time_start) <= 1
        if was_last_login_recent:
            return

        limiter.actions[GLOBAL] = (1.0, new_time_start, rate_hz)
