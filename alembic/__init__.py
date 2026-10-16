from urllib.parse import quote_plus

from pydantic import SecretStr
from sqlalchemy.engine.url import URL
from synapse.module_api import ModuleApi

from alembic import command
from alembic.config import Config as AlembicConfig
from connect.base_config import BaseConfig


class Config(BaseConfig):
    user: SecretStr
    password: SecretStr
    db: SecretStr


class MigrationRunner:
    def __init__(self, config: dict, api: ModuleApi):
        Config.model_validate(config)
        if config["password"]:
            # https://github.com/sqlalchemy/alembic/issues/700
            encoded_password = quote_plus(config["password"]).replace("%", "%%")
        else:
            encoded_password = None

        url = URL.create(
            drivername="postgresql+psycopg2",
            username=config["user"],
            password=encoded_password,
            host="database-primary",
            port=5432,
            database=config["db"],
        )

        alembic_cfg = AlembicConfig("/connect/alembic.ini")
        alembic_cfg.set_main_option("script_location", "/connect/alembic")
        alembic_cfg.set_main_option(
            "sqlalchemy.url", url.render_as_string(hide_password=False)
        )

        command.upgrade(alembic_cfg, "head")
