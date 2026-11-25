from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from pathlib import Path
import logging
import os

from database.models import Base

logger = logging.getLogger(__name__)


class Database:
    """SQLite database manager with high-concurrency settings."""
    
    def __init__(self, db_path: str = "data/trading_bot.db"):
        # Ensure directory exists
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        self.db_path = db_path
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            echo=False,
            connect_args={"timeout": 30.0, "check_same_thread": False}
        )

        # Enable WAL mode for high concurrency between processes
        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        self._SessionFactory = sessionmaker(bind=self.engine, expire_on_commit=False)
        logger.info(f"Database initialized at {db_path} with WAL mode enabled")
    
    def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self.engine)
        # `create_all` does not evolve existing SQLite tables.  These unique
        # indexes enforce the two durable idempotency keys without requiring a
        # destructive migration of an existing local trading journal.
        with self.engine.begin() as connection:
            duplicate_signal = connection.execute(text(
                "SELECT signal_id FROM trades GROUP BY signal_id HAVING COUNT(*) > 1 LIMIT 1"
            )).first()
            duplicate_ticket = connection.execute(text(
                "SELECT ticket FROM trades WHERE ticket IS NOT NULL "
                "GROUP BY ticket HAVING COUNT(*) > 1 LIMIT 1"
            )).first()
            if duplicate_signal is None:
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_signal_id ON trades(signal_id)"
                ))
            else:
                logger.error("Cannot enforce trades.signal_id uniqueness; duplicate %s exists.", duplicate_signal[0])
            if duplicate_ticket is None:
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_ticket "
                    "ON trades(ticket) WHERE ticket IS NOT NULL"
                ))
            else:
                logger.error("Cannot enforce trades.ticket uniqueness; duplicate %s exists.", duplicate_ticket[0])
        logger.info("Database tables created/verified")
    
    @contextmanager
    def session(self):
        """Provide a transactional session scope."""
        session = self._SessionFactory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()
    
    def get_session(self) -> Session:
        """Get a new session (caller must manage lifecycle)."""
        return self._SessionFactory()
