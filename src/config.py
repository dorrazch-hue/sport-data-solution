"""
Configuration centrale du projet Sport Data Solution.
Toutes les variables sont lues depuis le fichier .env.
Les paramètres métier sont modifiables sans toucher au code.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Charger le .env depuis la racine du projet
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


# =============================================================
# Chemins du projet
# =============================================================
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
LOGS_DIR = PROJECT_ROOT / "logs"

RH_FILE = RAW_DATA_DIR / "donnees_rh.xlsx"
SPORT_FILE = RAW_DATA_DIR / "donnees_sportives.xlsx"


# =============================================================
# Base de données PostgreSQL
# Dans Airflow, les variables sont exposées sous AIRFLOW_VAR_*
# On les lit en priorité pour que les tâches DAG pointent vers "postgres"
# et non vers "localhost" (qui ne résout rien dans le réseau Docker).
# =============================================================
def _get_env(key: str, default: str) -> str:
    """Lit d'abord la variable directe, sinon le préfixe AIRFLOW_VAR_."""
    return os.getenv(key) or os.getenv(f"AIRFLOW_VAR_{key}", default)

DB_HOST = _get_env("DB_HOST", "localhost")
DB_PORT = int(_get_env("DB_PORT", "5432"))
DB_NAME = _get_env("DB_NAME", "sport_data")
DB_USER = _get_env("DB_USER", "sport_user")
DB_PASSWORD = _get_env("DB_PASSWORD", "changeme_password")

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


# =============================================================
# APIs externes
# =============================================================
GOOGLE_MAPS_API_KEY = _get_env("GOOGLE_MAPS_API_KEY", "")
SLACK_BOT_TOKEN = _get_env("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = _get_env("SLACK_CHANNEL", "#sport-activites")


# =============================================================
# Paramètres métier (configurables dans .env)
# =============================================================

# Prime sportive : taux appliqué au salaire brut annuel
SPORT_BONUS_RATE = float(_get_env("SPORT_BONUS_RATE", "0.05"))

# Jours bien-être : nombre d'activités minimum pour être éligible
WELLNESS_DAYS_THRESHOLD = int(_get_env("WELLNESS_DAYS_THRESHOLD", "15"))

# Jours bien-être : nombre de jours accordés
WELLNESS_DAYS_COUNT = int(_get_env("WELLNESS_DAYS_COUNT", "5"))

# Adresse de l'entreprise (référence pour validation des trajets)
COMPANY_ADDRESS = _get_env("COMPANY_ADDRESS", "1362 Av. des Platanes, 34970 Lattes, France")

# Distance maximale autorisée selon mode de transport (km)
MAX_DISTANCE_WALKING_KM = float(_get_env("MAX_DISTANCE_WALKING_KM", "15.0"))
MAX_DISTANCE_CYCLING_KM = float(_get_env("MAX_DISTANCE_CYCLING_KM", "25.0"))


# =============================================================
# Modes de déplacement → catégories métier
# =============================================================
# Modes éligibles à la prime sportive
SPORT_COMMUTE_MODES = ["Marche/running", "Vélo/Trottinette/Autres"]

# Modes avec limite de distance
COMMUTE_DISTANCE_LIMITS = {
    "Marche/running": MAX_DISTANCE_WALKING_KM,      # ≤ 15 km
    "Vélo/Trottinette/Autres": MAX_DISTANCE_CYCLING_KM,  # ≤ 25 km
}


# =============================================================
# Types de sports → profil d'activité
# Utilisé pour la génération des données simulées
# distance_range : (min_m, max_m) ou None si non applicable
# pace_min_per_km : rythme en minutes pour calculer la durée
# =============================================================
SPORT_PROFILES = {
    "Course à pied": {
        "distance_range": (3000, 25000),
        "pace_min_per_km": (5.0, 7.5),
        "with_distance": True,
    },
    "Running": {  # Normalisation de "Runing"
        "distance_range": (3000, 25000),
        "pace_min_per_km": (5.0, 7.5),
        "with_distance": True,
    },
    "Randonnée": {
        "distance_range": (8000, 30000),
        "pace_min_per_km": (18.0, 28.0),
        "with_distance": True,
    },
    "Natation": {
        "distance_range": (500, 3500),
        "pace_min_per_km": (20.0, 35.0),  # min/km équivalent
        "with_distance": True,
    },
    "Triathlon": {
        "distance_range": (20000, 80000),
        "pace_min_per_km": (4.0, 6.0),
        "with_distance": True,
    },
    "Équitation": {
        "distance_range": (5000, 20000),
        "pace_min_per_km": (6.0, 15.0),
        "with_distance": True,
    },
    "Tennis": {
        "distance_range": None,
        "duration_range_min": (60, 120),
        "with_distance": False,
    },
    "Football": {
        "distance_range": None,
        "duration_range_min": (90, 120),
        "with_distance": False,
    },
    "Rugby": {
        "distance_range": None,
        "duration_range_min": (80, 100),
        "with_distance": False,
    },
    "Badminton": {
        "distance_range": None,
        "duration_range_min": (60, 90),
        "with_distance": False,
    },
    "Voile": {
        "distance_range": None,
        "duration_range_min": (120, 360),
        "with_distance": False,
    },
    "Judo": {
        "distance_range": None,
        "duration_range_min": (60, 90),
        "with_distance": False,
    },
    "Boxe": {
        "distance_range": None,
        "duration_range_min": (60, 90),
        "with_distance": False,
    },
    "Escalade": {
        "distance_range": None,
        "duration_range_min": (90, 180),
        "with_distance": False,
    },
    "Tennis de table": {
        "distance_range": None,
        "duration_range_min": (60, 90),
        "with_distance": False,
    },
    "Basketball": {
        "distance_range": None,
        "duration_range_min": (90, 120),
        "with_distance": False,
    },
}


# =============================================================
# Messages Slack par type de sport
# {prenom}, {nom}, {distance_km}, {duree_min}, {commentaire}
# =============================================================
SLACK_TEMPLATES = {
    "Course à pied": [
        "Bravo {prenom} {nom} ! Tu viens de courir {distance_km} km en {duree_min} min ! Quelle énergie ! 🔥🏅",
        "🏃 {prenom} {nom} vient de terminer une course de {distance_km} km en {duree_min} min. Chapeau ! 💪",
    ],
    "Running": [
        "Bravo {prenom} {nom} ! Tu viens de courir {distance_km} km en {duree_min} min ! Quelle énergie ! 🔥🏅",
    ],
    "Randonnée": [
        "Magnifique {prenom} {nom} ! Une randonnée de {distance_km} km terminée et un nouveau spot à découvrir ! 🌄{commentaire_fmt}",
        "🥾 {prenom} {nom} revient d'une randonnée de {distance_km} km. Bravo la forme ! 🌿",
    ],
    "Natation": [
        "🏊 Impressionnant {prenom} {nom} ! {distance_km_natation} m à la nage en {duree_min} min ! Magnifique ! 🌊",
    ],
    "Triathlon": [
        "🏊🚴🏃 Phénoménal {prenom} {nom} ! Un triathlon de {distance_km} km bouclé ! Légende du bureau ! 🏆",
    ],
    "Équitation": [
        "🐴 {prenom} {nom} vient de parcourir {distance_km} km à cheval. Une belle balade ! 🌳",
    ],
    "Tennis": [
        "🎾 {prenom} {nom} vient de jouer au tennis pendant {duree_min} min. Match nul ou victoire ? 😄",
    ],
    "Football": [
        "⚽ {prenom} {nom} a joué au football pendant {duree_min} min. On espère que l'équipe a gagné ! 🏆",
    ],
    "Rugby": [
        "🏉 {prenom} {nom} vient d'encaisser quelques placages au rugby ({duree_min} min). Costaud(e) ! 💪",
    ],
    "Badminton": [
        "🏸 {prenom} {nom} a battu ses adversaires sur le terrain de badminton ({duree_min} min) ! Smash ! 💥",
    ],
    "Voile": [
        "⛵ {prenom} {nom} a navigué pendant {duree_min} min. Le vent était avec toi ? 🌊",
    ],
    "Judo": [
        "🥋 {prenom} {nom} a pratiqué le judo pendant {duree_min} min. Ippon ! 🎯",
    ],
    "Boxe": [
        "🥊 {prenom} {nom} a boxé pendant {duree_min} min. KO technique de la sédentarité ! 💥",
    ],
    "Escalade": [
        "🧗 {prenom} {nom} vient de gravir des parois en {duree_min} min. Quel mental ! 🏔️",
    ],
    "Tennis de table": [
        "🏓 {prenom} {nom} a joué au ping-pong pendant {duree_min} min. Balle au bond ! 🎯",
    ],
    "Basketball": [
        "🏀 {prenom} {nom} vient de jouer au basketball ({duree_min} min). Swish ! 🎯",
    ],
}

DEFAULT_SLACK_TEMPLATE = "💪 Bravo {prenom} {nom} ! Séance de {sport} terminée ({duree_min} min). Bravo pour cette activité ! 🌟"


# =============================================================
# Environnement
# =============================================================
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT == "production"
