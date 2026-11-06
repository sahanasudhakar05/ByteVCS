from logging.config import fileConfig

from alembic import context

from api.database import Base, engine
import api.models  # noqa: F401  (registers the models)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    if engine is None:
        raise RuntimeError("DATABASE_URL is not set in .env")
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()