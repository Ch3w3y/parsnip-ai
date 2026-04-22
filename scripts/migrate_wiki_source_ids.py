import os
import psycopg
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://agent:ntjmke6b@localhost:5432/agent_kb")

def migrate_in_batches(batch_size=10000):
    logger.info("Starting Wikipedia source_id migration in batches...")
    conn = psycopg.connect(DB_URL)
    conn.autocommit = True
    
    total_updated = 0
    while True:
        try:
            with conn.cursor() as cur:
                # Update a batch of rows
                cur.execute(f"""
                    WITH batch AS (
                        SELECT id FROM knowledge_chunks 
                        WHERE source = 'wikipedia' AND source_id LIKE '%::%' 
                        LIMIT {batch_size}
                    )
                    UPDATE knowledge_chunks
                    SET source_id = split_part(source_id, '::', 1),
                        chunk_index = split_part(source_id, '::', 2)::int
                    WHERE id IN (SELECT id FROM batch);
                """)
                
                updated = cur.rowcount
                total_updated += updated
                logger.info(f"Updated {updated} rows. Total so far: {total_updated}")
                
                if updated == 0:
                    logger.info("Migration complete.")
                    break
                
                # Small sleep to allow other transactions to interleave
                time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error during migration: {e}")
            break
            
    conn.close()

if __name__ == "__main__":
    migrate_in_batches()
