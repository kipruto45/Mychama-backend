import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def connect():
    database_url = os.getenv("DATABASE_POOL_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_POOL_URL or DATABASE_URL must be set.")

    db_sslmode = os.getenv("DB_SSLMODE", "prefer")
    db_sslrootcert = os.getenv("DB_SSLROOTCERT")

    return psycopg2.connect(
        database_url,
        sslmode=db_sslmode,
        sslrootcert=db_sslrootcert
    )


if __name__ == "__main__":
    connection = connect()
    print("Connected to database.")
    connection.close()
