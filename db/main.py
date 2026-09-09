"""Class for setting up connections to the connect database tables"""

from typing import List

from pydantic import Field, SecretStr
from sqlalchemy import create_engine
from sqlalchemy import engine as sql_engine
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateSchema
from synapse.logging import logging
from synapse.module_api import ModuleApi

from ..base_config import BaseConfig
from .models import UserProfile


class Config(BaseConfig):
    """Configuration for database setup."""

    user: SecretStr
    password: SecretStr
    db: SecretStr
    tables: list[str] = Field(default_factory=list)


logger = logging.getLogger()


class Setup:
    """For database initialization and connections

    To use SQLAlchemy in other models, call `create_engine`, passing in the
    database args

    ```python
    engine = Setup.create_engine(
        user="postgres",
        password="postgres_password",
        db="synapse"
    )
    ```

    `engine` now can connect to the synapse database as the postgres user
    """  # noqa: E501, RST214, RST215, RST301, RST201, RST203

    def __init__(self, config: dict, api: ModuleApi):
        """Initializes connect schema in the synapse database.

        It connects to the "connect" schema by default to create tables.

        Args:
            config: module configs defined in homeserver.yaml. It includes values for database connection and a list of tables
            api: https://github.com/matrix-org/synapse/blob/9f7d6c6bc1b414d8f6591cc1d312a9c6b3a28980/synapse/module_api/__init__.py#L228
        """  # noqa: E501
        Config.model_validate(config)
        engine = Setup.create_engine(**config)

        with engine.begin() as con:
            if "connect" not in inspect(engine).get_schema_names():
                con.execute(CreateSchema("connect"))

        Setup._set_up_tables(engine, config["tables"])

    @staticmethod
    def parse_config(config: dict):
        """https://matrix-org.github.io/synapse/latest/modules/writing_a_module.html?highlight=ModuleApi#handling-the-modules-configuration

        Args:
            config: see link

        Returns:
            see link
        """  # noqa: E501
        return config

    @staticmethod
    def create_engine(**kwargs) -> Engine:
        """Utility method for creating and connecting an engine.

        Args:
            kwargs:
                user:
                    str - the database username
                password:
                    str - password for the username
                db:
                    str - name of the database

        Returns:
            a SQLAlchemy engine object that is connected to the database
        """
        url = sql_engine.URL.create(
            drivername="postgresql+psycopg2",
            username=kwargs.get("user"),
            password=kwargs.get("password"),
            host="database-primary",
            port=5432,
            database=kwargs.get("db"),
        )
        engine = create_engine(url, echo=True)
        return engine

    @staticmethod
    def _set_up_tables(engine: Engine, tables: List[str]):
        """Private utility method for creating tables

        Args:
            engine:
                SQLAlchemy Engine
            tables:
                list of table names to create
        """
        for table in tables:
            if not inspect(engine).has_table(table):
                UserProfile.metadata.create_all(engine)
