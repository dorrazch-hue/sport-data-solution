"""
Génération des activités sportives simulées (12 mois d'historique).

Objectif : simuler des données type Strava pour les 95 salariés
ayant déclaré un sport. Génère ~3 000 lignes réalistes.

Règles de génération :
  - 60% des sportifs → ≥ 15 activités/an (éligibles jours bien-être)
  - 40% des sportifs → 5 à 14 activités/an (non éligibles)
  - Distribution saisonnière réaliste (sports outdoor en été)
  - Distance et durée cohérentes avec le sport pratiqué
  - Commentaires occasionnels (20% des activités)
  - Activités en semaine ET week-end (réalisme)

Usage : python -m src.generation.generate_activities
"""

import sys
import random
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.orm import Session
from src.config import SPORT_PROFILES, WELLNESS_DAYS_THRESHOLD
from src.database.init_db import get_engine
from src.database.models import Employee, SportActivity, PipelineRun

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("generate_activities")

# Graine pour la reproductibilité
random.seed(42)
np.random.seed(42)

# Période de simulation : 12 derniers mois
END_DATE = datetime.utcnow().replace(hour=23, minute=59, second=59)
START_DATE = END_DATE - timedelta(days=365)

# Commentaires réalistes par sport
COMMENTS_BY_SPORT = {
    "Course à pied": [
        "Super séance du matin !",
        "Reprise progressive après le week-end",
        "Sortie longue, les jambes répondent bien",
        "Il faisait frais mais quelle belle sortie !",
        "Séance fractionnée au parc",
        "Course sous la pluie, courage quand même ! 🌧️",
        "Nouveau PR sur 10km ! 🏅",
        None, None, None,  # 30% de chances de pas de commentaire
    ],
    "Running": [
        "Super sortie matinale",
        "Reprise du sport :)",
        "Bonne allure aujourd'hui",
        None, None, None,
    ],
    "Randonnée": [
        "Randonnée de st Guilhem le desert, je vous la conseille c'est top",
        "Magnifique panorama depuis le sommet !",
        "Balade avec des amis, super ambiance",
        "Découverte d'un nouveau sentier 🌿",
        "Vue imprenable sur le littoral",
        None, None,
    ],
    "Natation": [
        "Bonne séance en piscine",
        "Travail sur la technique de crawl",
        "Séance endurance, 50 longueurs",
        None, None, None,
    ],
    "Tennis": [
        "Victoire 6-4 6-2 !",
        "Petit match amical entre collègues",
        "Travail du revers, encore des progrès à faire",
        None, None,
    ],
    "Football": [
        "Match de championnat, victoire 2-1 !",
        "Entraînement technique avec l'équipe",
        "Match amical, bonne ambiance",
        None, None,
    ],
    "Rugby": [
        "Entraînement avant le match du samedi",
        "Bonne séance de mêlées",
        None, None,
    ],
    "Escalade": [
        "7a enchaîné pour la première fois ! 🎉",
        "Séance bloc au gymnase",
        "Bonne progression sur les voies de dalle",
        None,
    ],
    "Triathlon": [
        "Triathlon de Palavas, finish ! 🏊🚴🏃",
        "Entraînement fractionné transition",
        None,
    ],
    "Badminton": [
        "Victoire en double !",
        "Bon match, belles frappes",
        None, None,
    ],
    "Voile": [
        "Belle brise aujourd'hui, conditions parfaites ⛵",
        "Sortie en mer avec l'équipage",
        None,
    ],
    "Judo": [
        "Compétition régionale, médaille de bronze 🥉",
        "Travail du tokui-waza",
        None, None,
    ],
    "Boxe": [
        "Sparring à l'entraînement",
        "Travail sur les combinaisons",
        None, None,
    ],
    "Équitation": [
        "Balade dans la garrigue",
        "Séance dressage, progrès notables",
        None,
    ],
    "Tennis de table": [
        "Victoire en championnat par équipes",
        "Bonne séance de service",
        None,
    ],
    "Basketball": [
        "Match de 3vs3 au city stade",
        "Entraînement tirs en suspension",
        None,
    ],
}


# Poids saisonniers par mois (0=Jan, 11=Dec)
# Sports outdoor → moins actifs en hiver
# Sports indoor → distribution uniforme
OUTDOOR_SPORTS = {"Course à pied", "Running", "Randonnée", "Triathlon", "Équitation", "Voile"}
INDOOR_SPORTS = {"Tennis", "Football", "Rugby", "Badminton", "Judo", "Boxe", "Natation",
                 "Escalade", "Tennis de table", "Basketball"}

# Poids par mois (index 0=Jan, index 11=Dec) pour sports outdoor
OUTDOOR_MONTHLY_WEIGHTS = [
    0.5, 0.6, 0.8, 1.0, 1.2, 1.3,   # Jan → Juin (monte progressivement)
    1.3, 1.2, 1.1, 1.0, 0.7, 0.5    # Juil → Dec (descend progressivement)
]

# Sports indoor : distribution quasi-uniforme
INDOOR_MONTHLY_WEIGHTS = [1.0] * 12

# On évite les vacances (moins d'activité déclarée pendant les congés)
# Août légèrement plus bas pour sports indoor (vacances), plus haut pour outdoor
OUTDOOR_MONTHLY_WEIGHTS[7] = 1.4  # Août : vacances + sport outdoor
INDOOR_MONTHLY_WEIGHTS[7] = 0.8   # Août : vacances, moins en salle


def get_monthly_weight(month: int, sport_type: str) -> float:
    """Retourne le poids de probabilité pour un mois donné selon le sport."""
    idx = month - 1  # 1=Jan → 0
    if sport_type in OUTDOOR_SPORTS:
        return OUTDOOR_MONTHLY_WEIGHTS[idx]
    return INDOOR_MONTHLY_WEIGHTS[idx]


def pick_random_date(start: datetime, end: datetime, sport_type: str) -> datetime:
    """
    Choisit une date aléatoire dans l'intervalle [start, end],
    en tenant compte de la saisonnalité du sport.
    """
    # Générer une liste de candidats et pondérer par mois
    days_range = (end - start).days
    if days_range <= 0:
        return start

    # Sélectionner un jour au hasard, pondéré par la saison
    candidate_days = list(range(days_range))
    weights = []
    for d in candidate_days:
        dt = start + timedelta(days=d)
        w = get_monthly_weight(dt.month, sport_type)
        # Éviter les activités très tôt le matin (probabilité plus faible)
        weights.append(w)

    chosen_day = random.choices(candidate_days, weights=weights, k=1)[0]
    chosen_date = start + timedelta(days=chosen_day)

    # Heure réaliste : 6h00 → 21h00
    hour = random.choices(
        range(6, 22),
        weights=[2, 2, 3, 4, 3, 2, 1, 1, 1, 2, 3, 4, 4, 3, 2, 2],
        k=1
    )[0]
    minute = random.choice([0, 15, 30, 45])

    return chosen_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


def generate_activity_stats(sport_type: str) -> tuple[Optional[int], int]:
    """
    Génère des statistiques réalistes pour une activité donnée.
    Retourne (distance_meters, duration_seconds).
    """
    profile = SPORT_PROFILES.get(sport_type)
    if not profile:
        logger.debug("  Profil sport inconnu : %s — utilisation des valeurs par défaut", sport_type)
        return None, random.randint(45, 90) * 60

    if profile["with_distance"]:
        d_min, d_max = profile["distance_range"]
        distance_m = int(random.gauss((d_min + d_max) / 2, (d_max - d_min) / 6))
        distance_m = max(d_min, min(d_max, distance_m))  # Clamp dans l'intervalle

        pace_min, pace_max = profile["pace_min_per_km"]
        pace = random.uniform(pace_min, pace_max)  # minutes par km
        duration_s = int((distance_m / 1000) * pace * 60)

        # Ajouter un peu de bruit sur la durée
        duration_s = int(duration_s * random.uniform(0.95, 1.05))
        return distance_m, duration_s

    else:
        dur_min, dur_max = profile.get("duration_range_min", (60, 90))
        duration_s = random.randint(dur_min, dur_max) * 60
        return None, duration_s


def get_comment(sport_type: str) -> Optional[str]:
    """Retourne un commentaire aléatoire pour le sport donné (peut être None)."""
    comments = COMMENTS_BY_SPORT.get(sport_type, [None, None, None])
    return random.choice(comments)


def generate_activities_for_employee(
    employee: Employee,
    n_activities: int,
) -> list[SportActivity]:
    """
    Génère n_activities activités réalistes pour un employé donné.
    """
    sport_type = employee.declared_sport
    activities = []

    # S'assurer qu'il n'y a pas deux activités le même jour
    used_dates: set[date] = set()

    attempts = 0
    max_attempts = n_activities * 5  # Éviter une boucle infinie

    while len(activities) < n_activities and attempts < max_attempts:
        attempts += 1

        activity_start = pick_random_date(START_DATE, END_DATE, sport_type)

        # Éviter les doublons sur le même jour
        if activity_start.date() in used_dates:
            continue
        used_dates.add(activity_start.date())

        distance_m, duration_s = generate_activity_stats(sport_type)

        # Date de fin = date de début + durée
        activity_end = activity_start + timedelta(seconds=duration_s)

        comment = get_comment(sport_type)

        activity = SportActivity(
            employee_id=employee.employee_id,
            activity_start=activity_start,
            activity_end=activity_end,
            sport_type=sport_type,
            distance_meters=distance_m,
            duration_seconds=duration_s,
            comment=comment,
            is_live=False,
            slack_sent=False,
        )
        activities.append(activity)

    if len(activities) < n_activities:
        logger.debug(
            "  Note : %d activités générées pour %s au lieu de %d (pas assez de jours distincts)",
            len(activities), employee.full_name, n_activities
        )

    return activities


def determine_activity_count(employee_index: int, total_employees: int) -> int:
    """
    Détermine le nombre d'activités à générer pour un employé.
    60% des employés → éligibles (≥ WELLNESS_THRESHOLD)
    40% des employés → non éligibles (< WELLNESS_THRESHOLD)
    """
    eligible_ratio = 0.60
    cutoff = int(total_employees * eligible_ratio)

    if employee_index < cutoff:
        # Éligible : 15 à 55 activités (distribution gaussienne centrée sur 28)
        n = int(random.gauss(28, 8))
        return max(WELLNESS_DAYS_THRESHOLD, min(55, n))
    else:
        # Non éligible : 3 à 14 activités
        return random.randint(3, WELLNESS_DAYS_THRESHOLD - 1)


def run_generation(run_id: str = None, force_regenerate: bool = False) -> dict:
    """
    Génère les activités sportives pour tous les salariés avec un sport déclaré.
    Si force_regenerate=True, supprime les données simulées existantes d'abord.
    """
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    started_at = datetime.utcnow()
    step_name = "generate_activities"

    logger.info("=" * 60)
    logger.info("ÉTAPE : Génération des activités sportives (simulation 12 mois)")
    logger.info("Run ID : %s", run_id)
    logger.info("Période : %s → %s", START_DATE.strftime("%d/%m/%Y"), END_DATE.strftime("%d/%m/%Y"))
    logger.info("=" * 60)

    engine = get_engine()
    total_generated = 0
    errors = 0

    try:
        with Session(engine) as session:
            # Récupérer les employés avec un sport déclaré
            employees_with_sport = (
                session.query(Employee)
                .filter(Employee.declared_sport.isnot(None))
                .order_by(Employee.employee_id)
                .all()
            )

            logger.info(
                "%d salariés avec sport déclaré identifiés",
                len(employees_with_sport)
            )

            # Supprimer les anciennes données simulées si demandé
            if force_regenerate:
                deleted = session.query(SportActivity).filter_by(is_live=False).delete()
                session.commit()
                logger.info("  ♻️  %d activités simulées supprimées (force_regenerate)", deleted)

            # Vérifier si des données existent déjà
            existing_count = session.query(SportActivity).filter_by(is_live=False).count()
            if existing_count > 0 and not force_regenerate:
                logger.info(
                    "  ℹ️  %d activités simulées déjà en base. "
                    "Utiliser force_regenerate=True pour regénérer.",
                    existing_count
                )
                return {"status": "skipped", "reason": "data_already_exists", "count": existing_count}

            # Mélanger les employés pour que la distribution 60/40 soit aléatoire
            shuffled_employees = employees_with_sport.copy()
            random.shuffle(shuffled_employees)

            all_activities = []
            for i, emp in enumerate(shuffled_employees):
                n_activities = determine_activity_count(i, len(shuffled_employees))

                logger.info(
                    "[%d/%d] %s %s (%s) → %d activités",
                    i + 1, len(shuffled_employees),
                    emp.first_name, emp.last_name, emp.declared_sport, n_activities
                )

                try:
                    activities = generate_activities_for_employee(emp, n_activities)
                    all_activities.extend(activities)
                    total_generated += len(activities)
                except Exception as e:
                    errors += 1
                    logger.error(
                        "  ❌ Erreur génération pour %s : %s",
                        emp.employee_id, e
                    )

            # Insertion en batch pour les performances
            logger.info("Insertion de %d activités en base...", len(all_activities))
            session.bulk_save_objects(all_activities)
            session.commit()

            logger.info("=" * 60)
            logger.info(
                "✅ %d activités générées et insérées (%d erreurs)",
                total_generated, errors
            )

            # Stats par sport
            sport_stats = {}
            for a in all_activities:
                sport_stats[a.sport_type] = sport_stats.get(a.sport_type, 0) + 1
            for sport, count in sorted(sport_stats.items(), key=lambda x: -x[1]):
                logger.info("  %-25s : %d activités", sport, count)

        # Monitoring
        with Session(engine) as session:
            run = PipelineRun(
                run_id=run_id,
                step_name=step_name,
                status="success" if errors == 0 else "warning",
                records_processed=len(employees_with_sport),
                records_inserted=total_generated,
                errors_count=errors,
                duration_seconds=round((datetime.utcnow() - started_at).total_seconds(), 3),
                details={
                    "employees_with_sport": len(employees_with_sport),
                    "total_activities": total_generated,
                    "period_start": START_DATE.isoformat(),
                    "period_end": END_DATE.isoformat(),
                    "sport_breakdown": sport_stats,
                },
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()

        return {
            "status": "success",
            "total_generated": total_generated,
            "employees_processed": len(employees_with_sport),
            "errors": errors,
        }

    except Exception as e:
        logger.error("❌ Génération échouée : %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Génération des activités sportives simulées")
    parser.add_argument(
        "--force", action="store_true",
        help="Supprimer et regénérer toutes les activités simulées"
    )
    args = parser.parse_args()

    result = run_generation(force_regenerate=args.force)
    if result["status"] == "failed":
        sys.exit(1)
