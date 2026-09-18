"""The ownership marker the fixtures module stamps on the resources it creates.

Every fixtures-created resource is owned by exactly one ``run_id`` at a time. The
marker is the mechanism: a global account-data entry written at creation and
re-written on reclaim, recording the owning run. It is the single source of truth
for teardown (find a run's resources, or every fixtures resource) and for the
strict-ownership conflict check on fixed-name users — and, because only this
module ever writes it, a marked resource is provably fixtures-created, never a
real or populator-seeded account. The same concept generalizes to future
provisioned resources (rooms, ...): give them this marker and run/global cleanup
reaches them too.
"""

# Global account-data type recording the run that owns a fixtures resource. Its
# content is ``{"run_id": "<run_id>"}`` (see ``run_marker_content``).
RUN_MARKER = "com.powerhrg.fixtures.run"


def run_marker_content(run_id: str) -> dict:
    # The marker payload stamped on a resource owned by ``run_id``.
    return {"run_id": run_id}


def owner_from_marker(content) -> str | None:
    # The run_id recorded in a marker payload, or None if the marker is
    # absent/empty. Accepts the raw account-data content dict.
    if not content:
        return None
    return content.get("run_id")
