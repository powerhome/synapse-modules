"""Connect-specific logic for the Synapse homeserver."""
from setuptools import find_packages, setup

setup(
    name="connect",
    version="0.1.0",
    packages=find_packages(include=["connect", "connect.*"]),
)
