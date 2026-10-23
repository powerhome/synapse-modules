# Migrations

We use [Alembic](https://alembic.sqlalchemy.org) to upgrade and downgrade our custom `connect` schema in the Synapse database.

When Synapse starts, the `MigrationRunner` class (one of our custom modules) runs the command
```
alembic upgrade head
```
programmatically to upgrade the schema to the latest migration.

## Development

To use Alembic locally, you must prepare the virtual environment:
```
cd connect
python -m venv .venv
source .venv/bin/activate
pip install --upgrade -r requirements/format.txt -r requirements/lint.txt -r requirements/synapse.txt
```
Then you'll have access to `alembic`.

You can use the following tutorial to learn how to create and run migrations: https://alembic.sqlalchemy.org/en/latest/tutorial.html#create-a-migration-script

The command to downgrade to an older migration is
```
alembic downgrade <revision>
```
or
```
alembic downgrade base
```
to rollback all migrations.

## Configuration Changes

We had to make some minor configuration changes to support the `connect` schema.

In `alembic/env.py`, we removed the code
```
# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
```
to prevent Alembic's logging configuration from overriding Synapse's.
We also updated `run_migrations_online` by including `version_table_schema="connect"` when configuring the context.

In `alembic.ini`, we explicitly set `timezone = utc` for consistency;
```
sqlalchemy.url = postgresql://synapse@127.0.0.1:15432/synapse
```
to enable running the `alembic` command locally; and we removed the, now unused, logging configuration.
