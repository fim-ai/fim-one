from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from fim_agent.db.base import Base

# Import all models so Base.metadata knows about them
from fim_agent.web.models import (  # noqa: F401
    Agent,
    Conversation,
    Message,
    ModelConfig,
    User,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "sqlite:///./data/fim_agent.db")
    url = url.replace("sqlite+aiosqlite", "sqlite")
    url = url.replace("postgresql+asyncpg", "postgresql+psycopg2")
    return url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_url()
    connectable = create_engine(url)
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
