"""How far a deployment has got, in a form that survives a restart."""

from dataclasses import asdict, dataclass, field

from synapse.types import JsonDict

# Bumped whenever the checkpoint's shape changes, so a deployment resumed by
# newer code never misreads an older checkpoint.
CHECKPOINT_VERSION = 1


class IncompatibleCheckpointError(Exception):
    """A checkpoint written by a version of this module that this one can't read."""


@dataclass
class Checkpoint:
    """A deployment's progress: the next step to run, and what it has made.

    ``users``, ``rooms`` and ``events`` map scenario refs to what was
    provisioned, so a resumed deployment resolves refs exactly as the original
    run would have. ``created_users`` and ``created_rooms`` list exactly what
    this deployment created, so rollback and deprovisioning touch nothing else —
    which is what lets several deployments share one ``run_id``. ``active_ms``
    counts only time spent running, so a restart's downtime never counts
    against the deadline.
    """

    next_index: int = 0
    users: dict[str, JsonDict] = field(default_factory=dict)
    rooms: dict[str, str] = field(default_factory=dict)
    events: dict[str, str] = field(default_factory=dict)
    created_users: list[str] = field(default_factory=list)
    created_rooms: list[str] = field(default_factory=list)
    active_ms: int = 0

    def to_json(self) -> JsonDict:
        """The checkpoint as stored.

        Returns:
            JsonDict: The checkpoint, with its format version.
        """
        return {"version": CHECKPOINT_VERSION, **asdict(self)}

    @classmethod
    def from_json(cls, data: JsonDict | None) -> "Checkpoint":
        """Read a stored checkpoint; no checkpoint is a fresh start.

        Args:
            data (JsonDict | None): The stored checkpoint, if any.

        Returns:
            Checkpoint: The decoded checkpoint.

        Raises:
            IncompatibleCheckpointError: If it was written in another format.
        """
        if data is None:
            return cls()
        fields = dict(data)
        version = fields.pop("version", None)
        if version != CHECKPOINT_VERSION:
            raise IncompatibleCheckpointError(
                f"incompatible checkpoint (version {version}, expected "
                f"{CHECKPOINT_VERSION}); delete the deployment and run it again"
            )
        return cls(**fields)

    def add_created_user(self, mxid: str) -> None:
        # Recorded once, however many times a resumed step re-provisions it.
        if mxid not in self.created_users:
            self.created_users.append(mxid)

    def add_created_room(self, room_id: str) -> None:
        if room_id not in self.created_rooms:
            self.created_rooms.append(room_id)
