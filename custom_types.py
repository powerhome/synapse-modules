"""Custom Pydantic types for Connect."""

from typing import Annotated

from pydantic import Field

CUSTOM_EVENT_TYPE = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
]
