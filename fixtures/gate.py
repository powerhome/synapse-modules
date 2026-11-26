"""Keeps admissions and teardowns from overlapping in time."""

from http import HTTPStatus

from synapse.api.errors import Codes, SynapseError

# Stands for "every run" while a global teardown is in progress.
EVERY_RUN = object()


class AdmissionGate:
    """Serializes the in-flight part of admitting and tearing down fixtures.

    Every fixtures write happens on the main process, so the window in which an
    admission has checked the database but not yet recorded its deployment —
    or a teardown has checked for unfinished deployments but not yet finished —
    only ever overlaps another request on this same process. The gate closes
    that window: entering it is synchronous (no await between the check and
    the claim), so two requests cannot both pass.

    It complements the database checks rather than replacing them: the
    database covers deployments already recorded, the gate covers the ones
    mid-admission.
    """

    def __init__(self):
        self._admitting: dict[object, int] = {}
        self._admitting_keys: set[str] = set()
        self._admitting_localparts: set[str] = set()
        self._tearing_down: set[object] = set()

    def begin_admission(
        self, run_id: str, key: str | None, fixed_localparts: list[str]
    ) -> None:  # noqa: DAR401
        """Claim the right to admit a deployment of ``run_id``.

        Args:
            run_id (str): The deployment's run.
            key (str | None): Its idempotency key, if any.
            fixed_localparts (list[str]): The Fixed localparts it reserves.

        Raises:
            SynapseError: 409 if the run is being torn down, or the key or a Fixed localpart is mid-admission elsewhere.
        """  # noqa: DAR401, DAR402
        if EVERY_RUN in self._tearing_down or run_id in self._tearing_down:
            raise _conflict(f"run '{run_id}' is being torn down")
        if key is not None and key in self._admitting_keys:
            raise _conflict(f"a deployment with key '{key}' is being admitted")
        taken = self._admitting_localparts.intersection(fixed_localparts)
        if taken:
            raise _conflict(
                f"Fixed user {sorted(taken)[0]} is reserved by a deployment "
                "being admitted"
            )
        self._admitting[run_id] = self._admitting.get(run_id, 0) + 1
        if key is not None:
            self._admitting_keys.add(key)
        self._admitting_localparts.update(fixed_localparts)

    def end_admission(
        self, run_id: str, key: str | None, fixed_localparts: list[str]
    ) -> None:
        """Release what ``begin_admission`` claimed.

        Args:
            run_id (str): The deployment's run.
            key (str | None): Its idempotency key, if any.
            fixed_localparts (list[str]): The Fixed localparts it reserved.
        """
        remaining = self._admitting[run_id] - 1
        if remaining:
            self._admitting[run_id] = remaining
        else:
            del self._admitting[run_id]
        self._admitting_keys.discard(key)
        self._admitting_localparts.difference_update(fixed_localparts)

    def begin_teardown(self, run_id: str | None) -> None:
        """Claim the right to tear down ``run_id`` (``None``: every run).

        Args:
            run_id (str | None): The run to tear down, or ``None`` for all.

        Raises:
            SynapseError: 409 if a deployment of that run (or any, for all) is mid-admission.
        """  # noqa: DAR401, DAR402
        scope = EVERY_RUN if run_id is None else run_id
        busy = bool(self._admitting) if run_id is None else run_id in self._admitting
        if busy or scope in self._tearing_down:
            raise _conflict("a deployment of this run is being admitted")
        self._tearing_down.add(scope)

    def end_teardown(self, run_id: str | None) -> None:
        """Release what ``begin_teardown`` claimed.

        Args:
            run_id (str | None): The torn-down run, or ``None`` for all.
        """
        self._tearing_down.discard(EVERY_RUN if run_id is None else run_id)


def _conflict(message: str) -> SynapseError:
    return SynapseError(HTTPStatus.CONFLICT, message, Codes.UNKNOWN)
