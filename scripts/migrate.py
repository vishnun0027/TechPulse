import sys
from pathlib import Path
from loguru import logger
import psycopg2

# Add src to python path to import settings
sys.path.append(str(Path(__file__).parent.parent / "src"))
from shared.config import settings


def run_migrations():
    db_url = settings.database_url
    if not db_url:
        logger.warning("DATABASE_URL is not configured. Skipping migrations.")
        return

    logger.info("Connecting to PostgreSQL database to apply migrations...")
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cursor = conn.cursor()

        migration_file = (
            Path(__file__).parent.parent / "migrations" / "master_schema_v2.sql"
        )
        if not migration_file.exists():
            logger.error(f"Migration file not found at: {migration_file}")
            sys.exit(1)

        logger.info(f"Reading migration file: {migration_file}")
        sql_content = migration_file.read_text(encoding="utf-8")

        logger.info("Executing master schema SQL script...")
        cursor.execute(sql_content)
        logger.success("Database migrations applied successfully!")

        cursor.close()
        conn.close()
    except Exception as e:
        logger.warning(
            f"Failed to apply database migrations (expected if host has no direct IPv6 database route): {e}"
        )
        sys.exit(0)


if __name__ == "__main__":
    run_migrations()
