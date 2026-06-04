"""Populate UUID and snapshot fields for existing data.

This script should be run once after the migration to populate:
1. uuid_id for all existing providers, models, and probe_results
2. provider_name_at_probe and model_id_at_probe for existing probe_results
"""
import uuid
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


def populate_data():
    settings = get_settings()
    engine = create_engine(settings.sync_database_url, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        print("Starting data population...")

        # Populate providers.uuid_id
        print("\n1. Populating providers.uuid_id...")
        result = session.execute(text("SELECT id, uuid_id FROM providers"))
        providers = result.fetchall()
        updated = 0
        for row in providers:
            if row[1] is None:  # uuid_id is NULL
                provider_id = row[0]
                new_uuid = str(uuid.uuid4())
                session.execute(
                    text("UPDATE providers SET uuid_id = :uuid WHERE id = :id"),
                    {"uuid": new_uuid, "id": provider_id}
                )
                updated += 1
        print(f"   Updated {updated} providers")

        # Populate models.uuid_id
        print("\n2. Populating models.uuid_id...")
        result = session.execute(text("SELECT id, uuid_id FROM models"))
        models = result.fetchall()
        updated = 0
        for row in models:
            if row[1] is None:  # uuid_id is NULL
                model_id = row[0]
                new_uuid = str(uuid.uuid4())
                session.execute(
                    text("UPDATE models SET uuid_id = :uuid WHERE id = :id"),
                    {"uuid": new_uuid, "id": model_id}
                )
                updated += 1
        print(f"   Updated {updated} models")

        # Populate probe_results.uuid_id and snapshot fields
        print("\n3. Populating probe_results.uuid_id and snapshot fields...")
        result = session.execute(
            text("SELECT id, uuid_id, provider_id, model_id, provider_name_at_probe, model_id_at_probe FROM probe_results")
        )
        probe_results = result.fetchall()
        updated = 0
        for row in probe_results:
            result_id = row[0]
            uuid_id = row[1]
            provider_id = row[2]
            model_id = row[3]
            provider_name_at_probe = row[4]
            model_id_at_probe = row[5]

            needs_update = False
            update_data = {"id": result_id}

            # Generate UUID if needed
            if uuid_id is None:
                update_data["uuid"] = str(uuid.uuid4())
                needs_update = True

            # Populate snapshot fields if needed
            if provider_name_at_probe is None and provider_id is not None:
                provider = session.execute(
                    text("SELECT name FROM providers WHERE id = :id"),
                    {"id": provider_id}
                ).fetchone()
                if provider:
                    update_data["provider_name"] = provider[0]
                    needs_update = True

            if model_id_at_probe is None and model_id is not None:
                model = session.execute(
                    text("SELECT model_id FROM models WHERE id = :id"),
                    {"id": model_id}
                ).fetchone()
                if model:
                    update_data["model_id_str"] = model[0]
                    needs_update = True

            if needs_update:
                # Build dynamic UPDATE statement
                set_clauses = []
                if "uuid" in update_data:
                    set_clauses.append("uuid_id = :uuid")
                if "provider_name" in update_data:
                    set_clauses.append("provider_name_at_probe = :provider_name")
                if "model_id_str" in update_data:
                    set_clauses.append("model_id_at_probe = :model_id_str")

                if set_clauses:
                    update_sql = f"UPDATE probe_results SET {', '.join(set_clauses)} WHERE id = :id"
                    session.execute(text(update_sql), update_data)
                    updated += 1

        print(f"   Updated {updated} probe_results")

        # Commit all changes
        session.commit()
        print("\n✓ Data population completed successfully!")

        # Print summary
        print("\n=== Summary ===")
        result = session.execute(text("SELECT COUNT(*) FROM providers WHERE uuid_id IS NOT NULL"))
        print(f"Providers with UUID: {result.scalar()}")

        result = session.execute(text("SELECT COUNT(*) FROM models WHERE uuid_id IS NOT NULL"))
        print(f"Models with UUID: {result.scalar()}")

        result = session.execute(text("SELECT COUNT(*) FROM probe_results WHERE uuid_id IS NOT NULL"))
        print(f"Probe results with UUID: {result.scalar()}")

        result = session.execute(
            text("SELECT COUNT(*) FROM probe_results WHERE provider_name_at_probe IS NOT NULL")
        )
        print(f"Probe results with provider snapshot: {result.scalar()}")

        result = session.execute(
            text("SELECT COUNT(*) FROM probe_results WHERE model_id_at_probe IS NOT NULL")
        )
        print(f"Probe results with model snapshot: {result.scalar()}")

    except Exception as e:
        session.rollback()
        print(f"\n✗ Error: {e}")
        raise
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    populate_data()
