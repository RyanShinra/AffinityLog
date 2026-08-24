import asyncio
from collections.abc import Iterable
from logging.config import fileConfig

from alembic import context
from alembic.operations.ops import MigrationScript
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import ModelBase
from app.models import orm  # noqa: F401 — ensure models are registered

config = context.config
fileConfig(config.config_file_name)  # type: ignore[arg-type]

target_metadata = ModelBase.metadata


def process_revision_directives(
    _context: MigrationContext,
    _revision: str | Iterable[str | None] | Iterable[str],
    directives: list[MigrationScript],
) -> None:
    """Zero-padded sequential revision ids (001, 002, 003…) instead of GUID hashes."""
    if not directives:
        return
    scriptDirectory: ScriptDirectory = ScriptDirectory.from_config(config)
    max_rev_num: int = 0
    for rev in scriptDirectory.walk_revisions():
        if not rev.revision.isdigit():
            continue
        max_rev_num = max(max_rev_num, int(rev.revision))

    max_rev_num += 1  # We want the next one, lol
    max_rev_str: str = f"{max_rev_num:03d}"
    directives[0].rev_id = max_rev_str


def run_migrations_offline() -> None:
    url = settings.database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(
            lambda sync_conn: context.configure(
                connection=sync_conn,
                target_metadata=target_metadata,
                process_revision_directives=process_revision_directives,
            )
        )
        async with connection.begin():
            await connection.run_sync(lambda _: context.run_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
