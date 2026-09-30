from .db import open_pool, close_pool, db_connection

SQL = """
SELECT
    table_schema,
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY table_schema, table_name, ordinal_position
"""


def main() -> None:
    open_pool()
    try:
        with db_connection() as conn:
            rows = conn.execute(SQL).fetchall()

        current = None
        for row in rows:
            key = (row["table_schema"], row["table_name"])
            if key != current:
                current = key
                print(f"\n[{key[0]}.{key[1]}]")
            print(
                f"  {row['column_name']:<34} "
                f"{row['data_type']:<28} nullable={row['is_nullable']}"
            )
    finally:
        close_pool()


if __name__ == "__main__":
    main()
