def postgres_checkpoint_dsn(database_url: str) -> str:
    """Convert SQLAlchemy's psycopg URL to the libpq URL expected by PostgresSaver."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
