"""Base configuration for all Connect modules."""

from pydantic import BaseModel, ConfigDict


class BaseConfig(BaseModel):
    """Base configuration class with common settings for all modules."""

    model_config = ConfigDict(hide_input_in_errors=True)
