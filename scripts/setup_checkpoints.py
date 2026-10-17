from langgraph.checkpoint.postgres import PostgresSaver

from apps.agent_host.settings import get_settings
from supplymesh.workflows.checkpointing import postgres_checkpoint_dsn


def main() -> None:
    dsn = postgres_checkpoint_dsn(get_settings().database_url)
    with PostgresSaver.from_conn_string(dsn) as checkpointer:
        checkpointer.setup()


if __name__ == "__main__":
    main()
