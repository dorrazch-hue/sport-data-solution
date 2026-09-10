"""
Initialisation de la base de données PostgreSQL.
Crée toutes les tables si elles n'existent pas.
Usage : python -m src.database.init_db
"""

import sys
import logging
from pathlib import Path

# Assurer que la racine du projet est dans le PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import create_engine, text
import json as _json, numpy as _np

def _json_serial(obj):
    class Enc(_json.JSONEncoder):
        def default(self, o):
            if isinstance(o, _np.integer): return int(o)
            if isinstance(o, _np.floating): return float(o)
            if isinstance(o, _np.ndarray): return o.tolist()
            return super().default(o)
    return _json.dumps(obj, cls=Enc)
from sqlalchemy.exc import OperationalError
from src.config import DATABASE_URL, DB_NAME, DB_USER, DB_HOST, DB_PORT
from src.database.models import Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("init_db")


def test_connection(engine) -> bool:
    """Teste la connexion à la base de données."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ Connexion à PostgreSQL réussie (%s@%s:%s/%s)",
                    DB_USER, DB_HOST, DB_PORT, DB_NAME)
        return True
    except OperationalError as e:
        logger.error("❌ Impossible de se connecter à PostgreSQL : %s", e)
        logger.error("Vérifiez que le conteneur Docker est démarré : docker-compose up -d")
        return False


def create_all_tables(engine) -> None:
    """Crée toutes les tables définies dans les modèles SQLAlchemy."""
    logger.info("Création des tables en base de données...")
    Base.metadata.create_all(engine)
    logger.info("✅ Tables créées (ou déjà existantes) :")
    for table_name in Base.metadata.tables.keys():
        logger.info("   — %s", table_name)


def init_db() -> None:
    """Point d'entrée principal : initialise la base de données."""
    logger.info("=" * 60)
    logger.info("Initialisation de la base Sport Data Solution")
    logger.info("=" * 60)

    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        json_serializer=_json_serial,      # Vérification de la connexion avant chaque requête
        pool_size=5,
        max_overflow=10,
        echo=False               # Mettre True pour voir les requêtes SQL
    )

    if not test_connection(engine):
        sys.exit(1)

    create_all_tables(engine)

    logger.info("=" * 60)
    logger.info("✅ Base de données initialisée avec succès")
    logger.info("=" * 60)


def get_engine():
    """Retourne un moteur SQLAlchemy configuré. Utilisé par les autres modules."""
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        json_serializer=_json_serial,
        pool_size=5,
        max_overflow=10,
        echo=False
    )


if __name__ == "__main__":
    init_db()
