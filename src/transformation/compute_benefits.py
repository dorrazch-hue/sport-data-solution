"""
Calcul des avantages salariaux — Sport Data Solution.

Deux avantages calculés :

1. Prime sportive (5% du salaire brut annuel)
   Conditions d'éligibilité :
     - Le salarié déclare venir au bureau en faisant du sport
       (mode = "Marche/running" OU "Vélo/Trottinette/Autres")
     - Le trajet est validé comme cohérent (distance ≤ seuil autorisé)
   Montant : salaire_brut * SPORT_BONUS_RATE

2. 5 jours bien-être
   Conditions d'éligibilité :
     - Le salarié a déclaré pratiquer un sport
     - Le salarié a ≥ WELLNESS_DAYS_THRESHOLD activités dans l'année
   Accordés : WELLNESS_DAYS_COUNT jours

Impact financier :
   - Prime : montant direct en €
   - Jours bien-être : estimé à (salaire journalier × nombre de jours)
     Salaire journalier = salaire brut / 218 jours travaillés (convention France)

Usage : python -m src.transformation.compute_benefits
"""

import sys
import logging
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.orm import Session
from sqlalchemy import func
from src.config import (
    SPORT_BONUS_RATE, WELLNESS_DAYS_THRESHOLD, WELLNESS_DAYS_COUNT,
    SPORT_COMMUTE_MODES
)
from src.database.init_db import get_engine
from src.database.models import (
    Employee, CommuteValidation, SportActivity,
    BenefitCalculation, PipelineRun
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("compute_benefits")

# Nombre de jours ouvrés par an (convention France)
WORKING_DAYS_PER_YEAR = 218


def get_activity_count_for_employee(session: Session, employee_id: int, year: int) -> int:
    """Compte les activités sportives d'un salarié pour une année donnée."""
    count = (
        session.query(func.count(SportActivity.id))
        .filter(
            SportActivity.employee_id == employee_id,
            func.extract("year", SportActivity.activity_start) == year
        )
        .scalar()
    )
    return count or 0


def get_commute_validation(session: Session, employee_id: int) -> CommuteValidation:
    """Récupère la dernière validation de trajet pour un salarié."""
    return (
        session.query(CommuteValidation)
        .filter(CommuteValidation.employee_id == employee_id)
        .order_by(CommuteValidation.validated_at.desc())
        .first()
    )


def compute_sport_bonus(
    employee: Employee,
    commute_validation: CommuteValidation,
) -> dict:
    """
    Calcule l'éligibilité et le montant de la prime sportive.

    Retourne un dict avec :
      - eligible : bool
      - amount : float (€)
      - reason : str (explication)
      - commute_is_valid : bool
      - commute_distance_km : float
    """
    gross_salary = float(employee.gross_salary)

    # Étape 1 : vérifier le mode de déplacement
    if employee.commute_mode not in SPORT_COMMUTE_MODES:
        return {
            "eligible": False,
            "amount": 0.0,
            "reason": (
                f"Mode de déplacement '{employee.commute_mode}' non éligible à la prime sportive. "
                f"Modes éligibles : {', '.join(SPORT_COMMUTE_MODES)}"
            ),
            "commute_is_valid": None,
            "commute_distance_km": None,
        }

    # Étape 2 : vérifier la cohérence du trajet
    if commute_validation is None:
        return {
            "eligible": False,
            "amount": 0.0,
            "reason": "Trajet non encore validé — exécuter d'abord validate_commute",
            "commute_is_valid": None,
            "commute_distance_km": None,
        }

    commute_distance = float(commute_validation.distance_km) if commute_validation.distance_km else None

    if commute_validation.is_valid is False:
        return {
            "eligible": False,
            "amount": 0.0,
            "reason": (
                f"Trajet invalide — {commute_validation.anomaly_reason}. "
                f"Prime refusée jusqu'à régularisation."
            ),
            "commute_is_valid": False,
            "commute_distance_km": commute_distance,
        }

    if commute_validation.is_valid is None:
        # Validation impossible (adresse non géocodable) → bénéfice du doute accordé
        logger.warning(
            "  ⚠️  Validation incomplète pour %s — prime accordée avec réserve",
            employee.employee_id
        )

    # Éligible
    bonus_amount = round(gross_salary * SPORT_BONUS_RATE, 2)
    return {
        "eligible": True,
        "amount": bonus_amount,
        "reason": (
            f"Mode de déplacement sportif '{employee.commute_mode}' validé "
            f"(distance : {commute_distance:.1f} km) — prime de {SPORT_BONUS_RATE*100:.0f}% appliquée."
        ),
        "commute_is_valid": commute_validation.is_valid,
        "commute_distance_km": commute_distance,
    }


def compute_wellness_days(
    employee: Employee,
    activity_count: int,
) -> dict:
    """
    Calcule l'éligibilité aux jours bien-être.

    Retourne un dict avec :
      - eligible : bool
      - days : int
      - activity_count : int
      - reason : str
    """
    # Étape 1 : vérifier si un sport est déclaré
    if not employee.declared_sport:
        return {
            "eligible": False,
            "days": 0,
            "activity_count": 0,
            "reason": "Aucun sport déclaré — non éligible aux jours bien-être.",
        }

    # Étape 2 : vérifier le nombre d'activités
    if activity_count >= WELLNESS_DAYS_THRESHOLD:
        return {
            "eligible": True,
            "days": WELLNESS_DAYS_COUNT,
            "activity_count": activity_count,
            "reason": (
                f"{activity_count} activités de {employee.declared_sport} déclarées "
                f"(seuil : {WELLNESS_DAYS_THRESHOLD}) — {WELLNESS_DAYS_COUNT} jours bien-être accordés."
            ),
        }
    else:
        missing = WELLNESS_DAYS_THRESHOLD - activity_count
        return {
            "eligible": False,
            "days": 0,
            "activity_count": activity_count,
            "reason": (
                f"Seulement {activity_count} activités de {employee.declared_sport} déclarées "
                f"(minimum requis : {WELLNESS_DAYS_THRESHOLD}). "
                f"Il manque {missing} activité(s) pour être éligible."
            ),
        }


def compute_company_cost(
    gross_salary: float,
    sport_bonus_amount: float,
    wellness_days: int,
) -> float:
    """
    Estime le coût total pour l'entreprise (prime + valeur des jours bien-être).
    Les jours bien-être sont estimés au salaire journalier brut.
    """
    daily_rate = gross_salary / WORKING_DAYS_PER_YEAR
    wellness_cost = daily_rate * wellness_days
    total = sport_bonus_amount + wellness_cost
    return round(total, 2)


def run_computation(run_id: str = None, year: int = None) -> dict:
    """
    Calcule les avantages pour tous les salariés.
    Si year est None, utilise l'année courante.
    """
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if year is None:
        year = datetime.utcnow().year

    started_at = datetime.utcnow()
    step_name = "compute_benefits"

    logger.info("=" * 60)
    logger.info("ÉTAPE : Calcul des avantages salariaux — Année %d", year)
    logger.info("Run ID : %s", run_id)
    logger.info("Paramètres : prime=%.0f%% | bien-être=%d activités min → %d jours",
                SPORT_BONUS_RATE * 100, WELLNESS_DAYS_THRESHOLD, WELLNESS_DAYS_COUNT)
    logger.info("=" * 60)

    engine = get_engine()
    processed = 0
    inserted = 0
    errors = 0

    # Compteurs agrégés pour le rapport final
    total_eligible_bonus = 0
    total_eligible_wellness = 0
    total_cost_bonus = 0.0
    total_cost_wellness = 0.0
    total_anomalies = 0

    try:
        with Session(engine) as session:
            employees = session.query(Employee).order_by(Employee.employee_id).all()
            logger.info("Calcul des avantages pour %d salariés...", len(employees))

            for emp in employees:
                processed += 1
                logger.info(
                    "[%d/%d] %s %s (salaire : %d€, mode : %s, sport : %s)",
                    processed, len(employees),
                    emp.first_name, emp.last_name,
                    int(emp.gross_salary),
                    emp.commute_mode,
                    emp.declared_sport or "—"
                )

                try:
                    # Récupérer la validation de trajet
                    commute_validation = get_commute_validation(session, emp.employee_id)

                    # Compter les activités de l'année
                    activity_count = get_activity_count_for_employee(session, emp.employee_id, year)

                    # Calculer les avantages
                    bonus_result = compute_sport_bonus(emp, commute_validation)
                    wellness_result = compute_wellness_days(emp, activity_count)

                    # Coût total entreprise
                    total_cost = compute_company_cost(
                        gross_salary=float(emp.gross_salary),
                        sport_bonus_amount=bonus_result["amount"],
                        wellness_days=wellness_result["days"],
                    )

                    # Supprimer l'ancien calcul pour cette année si existant
                    session.query(BenefitCalculation).filter_by(
                        employee_id=emp.employee_id,
                        calculation_year=year
                    ).delete()

                    # Insérer le nouveau calcul
                    benefit = BenefitCalculation(
                        employee_id=emp.employee_id,
                        calculation_year=year,
                        # Prime sportive
                        eligible_sport_bonus=bonus_result["eligible"],
                        sport_bonus_amount=bonus_result["amount"],
                        sport_bonus_reason=bonus_result["reason"],
                        commute_distance_km=bonus_result["commute_distance_km"],
                        commute_is_valid=bonus_result["commute_is_valid"],
                        # Jours bien-être
                        activity_count_year=wellness_result["activity_count"],
                        eligible_wellness_days=wellness_result["eligible"],
                        wellness_days_count=wellness_result["days"],
                        wellness_reason=wellness_result["reason"],
                        # Impact financier
                        total_cost_company=total_cost,
                    )
                    session.add(benefit)
                    inserted += 1

                    # Agréger les stats
                    if bonus_result["eligible"]:
                        total_eligible_bonus += 1
                        total_cost_bonus += bonus_result["amount"]
                    if wellness_result["eligible"]:
                        total_eligible_wellness += 1
                        total_cost_wellness += (
                            float(emp.gross_salary) / WORKING_DAYS_PER_YEAR * wellness_result["days"]
                        )
                    if bonus_result["commute_is_valid"] is False:
                        total_anomalies += 1

                    # Logger le résultat
                    prime_str = f"Prime ✅ {bonus_result['amount']:.0f}€" if bonus_result["eligible"] else "Prime ❌"
                    wellness_str = f"Bien-être ✅ {wellness_result['days']}j" if wellness_result["eligible"] else f"Bien-être ❌ ({activity_count} actvités)"
                    logger.info("  → %s | %s", prime_str, wellness_str)

                except Exception as e:
                    errors += 1
                    logger.error("  ❌ Erreur calcul %s : %s", emp.employee_id, e)

            session.commit()

        # Rapport final
        logger.info("=" * 60)
        logger.info("RAPPORT AVANTAGES — ANNÉE %d", year)
        logger.info("=" * 60)
        logger.info("Salariés traités          : %d", processed)
        logger.info("")
        logger.info("--- PRIME SPORTIVE ---")
        logger.info("Éligibles prime           : %d / %d", total_eligible_bonus, processed)
        logger.info("Coût total primes (€)     : %,.2f €", total_cost_bonus)
        logger.info("Anomalies trajets          : %d", total_anomalies)
        logger.info("")
        logger.info("--- JOURS BIEN-ÊTRE ---")
        logger.info("Éligibles bien-être       : %d / %d", total_eligible_wellness, processed)
        logger.info("Jours accordés (total)    : %d", total_eligible_wellness * WELLNESS_DAYS_COUNT)
        logger.info("Coût jours bien-être (€)  : %,.2f €", total_cost_wellness)
        logger.info("")
        logger.info("COÛT TOTAL ESTIMÉ         : %,.2f €", total_cost_bonus + total_cost_wellness)
        logger.info("=" * 60)

        # Monitoring
        with Session(engine) as session:
            run = PipelineRun(
                run_id=run_id,
                step_name=step_name,
                status="success" if errors == 0 else "warning",
                records_processed=processed,
                records_inserted=inserted,
                errors_count=errors,
                duration_seconds=round((datetime.utcnow() - started_at).total_seconds(), 3),
                details={
                    "year": year,
                    "eligible_bonus": total_eligible_bonus,
                    "eligible_wellness": total_eligible_wellness,
                    "total_cost_bonus_eur": round(total_cost_bonus, 2),
                    "total_cost_wellness_eur": round(total_cost_wellness, 2),
                    "total_cost_eur": round(total_cost_bonus + total_cost_wellness, 2),
                    "commute_anomalies": total_anomalies,
                },
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()

        return {
            "status": "success",
            "year": year,
            "processed": processed,
            "eligible_bonus": total_eligible_bonus,
            "eligible_wellness": total_eligible_wellness,
            "total_cost_eur": round(total_cost_bonus + total_cost_wellness, 2),
            "commute_anomalies": total_anomalies,
            "errors": errors,
        }

    except Exception as e:
        logger.error("❌ Calcul avantages échoué : %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}


if __name__ == "__main__":
    result = run_computation()
    if result["status"] == "failed":
        sys.exit(1)
