"""The role a running scenario reports its steps through."""

from connect.fixtures.checkpoint import Checkpoint


class Journal:
    """Hears about each step of a scenario as the provisioner runs it.

    The runner's journal records checkpoints, paces the reactor, and stops a
    deployment (by raising from ``before_step``) when it is cancelled or past
    its deadline. This base class does none of that, for callers — tests —
    that just want the scenario run.
    """

    async def before_step(self, step, checkpoint: Checkpoint) -> None:
        """Called before each step, and before each retry of one.

        Args:
            step: The step about to run.
            checkpoint (Checkpoint): The deployment's progress so far.
        """

    async def after_step(self, step, checkpoint: Checkpoint) -> None:
        """Called once a step has finished and ``checkpoint`` includes it.

        Args:
            step: The step that finished.
            checkpoint (Checkpoint): The deployment's progress, including it.
        """

    def may_roll_back(self) -> bool:
        """Whether a failure should roll the deployment back.

        Returns:
            bool: False while the process is shutting down, when a failure is the shutdown's doing and the deployment should resume instead.
        """
        return True
