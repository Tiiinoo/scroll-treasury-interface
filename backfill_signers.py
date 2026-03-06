import sqlite3
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_PATH = os.environ.get("DATABASE_PATH", "treasury.db")

def backfill_missing_signers():
    """
    Finds all transactions that have empty signers but share a tx_hash 
    with a transaction that DOES have signers, and copies them over.
    """
    if not os.path.exists(DATABASE_PATH):
        logger.error(f"Database {DATABASE_PATH} not found.")
        return

    logger.info(f"Connecting to {DATABASE_PATH}...")
    conn = sqlite3.connect(DATABASE_PATH)
    
    try:
        cursor = conn.execute("""
            UPDATE transactions 
            SET signers = (
                SELECT signers FROM transactions t2 
                WHERE t2.tx_hash = transactions.tx_hash AND t2.signers != '' 
                LIMIT 1
            ) 
            WHERE signers = '' AND tx_hash IN (
                SELECT tx_hash FROM transactions WHERE signers != ''
            )
        """)
        conn.commit()
        updated_rows = cursor.rowcount
        logger.info(f"Successfully retro-stamped signers onto {updated_rows} split-transaction rows!")
    except Exception as e:
        logger.error(f"Error executing backfill: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    backfill_missing_signers()
