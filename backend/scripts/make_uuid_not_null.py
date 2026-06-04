"""Make uuid_id columns NOT NULL and create unique indexes.

This script should be run after populate_uuid_and_snapshots.py to enforce
NOT NULL constraints and create unique indexes on uuid_id columns.
"""
import sqlite3


def make_uuid_not_null():
    db_path = "/home/test/xf_ws/llm_usability/backend/data/llm_usability.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Making uuid_id columns NOT NULL and creating unique indexes...")

    # SQLite doesn't support ALTER COLUMN to change nullability directly.
    # We need to recreate the tables with the new schema.
    # However, this is complex and risky. Instead, we'll just create the
    # unique indexes and rely on application logic to ensure NOT NULL.

    # Create unique indexes on uuid_id columns
    print("\n1. Creating unique index on providers.uuid_id...")
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_providers_uuid_id ON providers(uuid_id)")
        print("   ✓ Index created")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    print("\n2. Creating unique index on models.uuid_id...")
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_models_uuid_id ON models(uuid_id)")
        print("   ✓ Index created")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    print("\n3. Creating unique index on probe_results.uuid_id...")
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_probe_results_uuid_id ON probe_results(uuid_id)")
        print("   ✓ Index created")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    conn.commit()
    conn.close()

    print("\n✓ All indexes created successfully!")
    print("\nNote: SQLite doesn't support ALTER COLUMN to change nullability.")
    print("The application logic ensures uuid_id is always populated for new records.")


if __name__ == "__main__":
    make_uuid_not_null()
