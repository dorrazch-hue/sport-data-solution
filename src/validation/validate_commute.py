"""
Validation des trajets domicile → bureau.

Deux modes de calcul de distance :
  1. Google Maps Distance Matrix API (si GOOGLE_MAPS_API_KEY est défini dans .env)
     → Distance réelle (à pied ou à vélo selon le mode)
  2. Haversine (fallback si pas de clé Google Maps)
     → Distance à vol d'oiseau × facteur de correction (1.35)

Règles métier :
  - Marche/Running  → distance ≤ 15 km
  - Vélo/Trottinette/Autres → distance ≤ 25 km
  - Autres modes (voiture, transports) → pas de limite, validation automatique

Usage : python -m src.validation.validate_commute
"""

import sys
import math
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.orm import Session
from src.config import (
    GOOGLE_MAPS_API_KEY, COMPANY_ADDRESS,
    COMMUTE_DISTANCE_LIMITS, SPORT_COMMUTE_MODES,
    MAX_DISTANCE_WALKING_KM, MAX_DISTANCE_CYCLING_KM
)
from src.database.init_db import get_engine
from src.database.models import Employee, CommuteValidation, PipelineRun

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("validate_commute")

# Coordonnées GPS de l'entreprise : 1362 Av. des Platanes, 34970 Lattes
COMPANY_LAT = 43.5773
COMPANY_LON = 3.9097

# Facteur de correction haversine → distance routière réelle
# (les routes ne sont pas à vol d'oiseau, surtout en zone urbaine)
HAVERSINE_CORRECTION_FACTOR = 1.35


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcule la distance à vol d'oiseau entre deux points GPS (formule haversine).
    Applique un facteur correctif pour approximer la distance réelle.
    Retourne la distance en kilomètres.
    """
    R = 6371.0  # Rayon de la Terre en km

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    straight_line_km = R * c
    return round(straight_line_km * HAVERSINE_CORRECTION_FACTOR, 3)


def geocode_address_haversine(address: str) -> Optional[tuple[float, float]]:
    """
    Géocode une adresse en utilisant geopy (Nominatim, sans clé API).
    Retourne (latitude, longitude) ou None si l'adresse est introuvable.
    """
    try:
        from geopy.geocoders import Nominatim
        from geopy.exc import GeocoderTimedOut, GeocoderUnavailable

        geolocator = Nominatim(user_agent="sport_data_solution_poc")
        time.sleep(1.1)  # Respecter la limite de 1 requête/seconde de Nominatim

        location = geolocator.geocode(address, timeout=10, country_codes="fr")
        if location:
            return (location.latitude, location.longitude)
        else:
            logger.warning("  ⚠️  Adresse introuvable (Nominatim) : %s", address)
            return None

    except (GeocoderTimedOut, GeocoderUnavailable) as e:
        logger.warning("  ⚠️  Géocodage Nominatim indisponible : %s", e)
        return None
    except Exception as e:
        logger.error("  ❌ Erreur géocodage : %s", e)
        return None


def get_distance_google_maps(home_address: str, commute_mode: str) -> Optional[float]:
    """
    Calcule la distance via Google Maps Distance Matrix API.
    Mode de transport : 'walking' pour marche, 'bicycling' pour vélo.
    Retourne la distance en km ou None en cas d'erreur.
    """
    try:
        import googlemaps

        client = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

        # Choisir le mode de transport Google Maps selon le mode déclaré
        if commute_mode == "Marche/running":
            travel_mode = "walking"
        elif commute_mode == "Vélo/Trottinette/Autres":
            travel_mode = "bicycling"
        else:
            travel_mode = "driving"

        result = client.distance_matrix(
            origins=[home_address],
            destinations=[COMPANY_ADDRESS],
            mode=travel_mode,
            language="fr",
            units="metric"
        )

        element = result["rows"][0]["elements"][0]

        if element["status"] == "OK":
            distance_m = element["distance"]["value"]
            return round(distance_m / 1000, 3)
        else:
            logger.warning(
                "  ⚠️  Google Maps n'a pas trouvé d'itinéraire pour : %s (status: %s)",
                home_address, element["status"]
            )
            return None

    except Exception as e:
        logger.error("  ❌ Erreur Google Maps API : %s", e)
        return None


def get_distance_km(home_address: str, commute_mode: str) -> tuple[Optional[float], str]:
    """
    Calcule la distance domicile-bureau selon la méthode disponible.
    Retourne (distance_km, method) où method = 'google_maps' ou 'haversine'.

    Stratégie :
      - Si GOOGLE_MAPS_API_KEY est défini → utilise Google Maps (plus précis)
      - Sinon → géocode avec Nominatim + formule haversine (estimation)
    """
    if GOOGLE_MAPS_API_KEY:
        logger.debug("  Utilisation de Google Maps API...")
        distance = get_distance_google_maps(home_address, commute_mode)
        if distance is not None:
            return distance, "google_maps"
        logger.warning("  Google Maps a échoué, bascule sur haversine...")

    # Fallback : haversine via Nominatim
    logger.debug("  Utilisation de la méthode haversine (Nominatim)...")
    coords = geocode_address_haversine(home_address)
    if coords:
        lat, lon = coords
        distance = haversine_distance_km(lat, lon, COMPANY_LAT, COMPANY_LON)
        return distance, "haversine"

    return None, "failed"


def validate_employee_commute(employee: Employee) -> dict:
    """
    Valide le trajet d'un salarié.
    Retourne un dictionnaire avec les résultats de la validation.
    """
    result = {
        "employee_id": employee.employee_id,
        "home_address": employee.home_address,
        "commute_mode": employee.commute_mode,
        "distance_km": None,
        "max_allowed_km": None,
        "is_valid": None,
        "anomaly_reason": None,
        "validation_method": "n/a",
    }

    # Modes non sportifs → pas de limite de distance → valide d'office
    if employee.commute_mode not in SPORT_COMMUTE_MODES:
        result["is_valid"] = True
        result["anomaly_reason"] = None
        result["validation_method"] = "no_check_needed"
        return result

    # Mode sportif → calcul de distance requis
    if not employee.home_address:
        result["is_valid"] = None
        result["anomaly_reason"] = "Adresse domicile manquante — validation impossible"
        return result

    max_km = COMMUTE_DISTANCE_LIMITS[employee.commute_mode]
    result["max_allowed_km"] = max_km

    distance_km, method = get_distance_km(employee.home_address, employee.commute_mode)
    result["validation_method"] = method

    if distance_km is None:
        result["is_valid"] = None
        result["anomaly_reason"] = "Distance impossible à calculer — adresse non géocodable"
        return result

    result["distance_km"] = distance_km

    if distance_km <= max_km:
        result["is_valid"] = True
    else:
        result["is_valid"] = False
        result["anomaly_reason"] = (
            f"Distance déclarée ({distance_km:.1f} km) supérieure au maximum autorisé "
            f"pour '{employee.commute_mode}' ({max_km:.0f} km). "
            f"Vérification nécessaire ou changement de situation."
        )

    return result


def run_validation(run_id: str = None) -> dict:
    """
    Valide les trajets de tous les salariés en base.
    Retourne les métriques d'exécution.
    """
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    started_at = datetime.utcnow()
    step_name = "validate_commutes"

    logger.info("=" * 60)
    logger.info("ÉTAPE : Validation des trajets domicile-bureau")
    logger.info("Run ID : %s", run_id)
    if GOOGLE_MAPS_API_KEY:
        logger.info("Mode : Google Maps Distance Matrix API")
    else:
        logger.info("Mode : Haversine (Nominatim) — Clé Google Maps non configurée")
        logger.info("       Pour plus de précision, configurer GOOGLE_MAPS_API_KEY dans .env")
    logger.info("Adresse entreprise : %s", COMPANY_ADDRESS)
    logger.info("=" * 60)

    engine = get_engine()
    processed = 0
    validated = 0
    anomalies = 0
    skipped = 0
    errors = 0

    try:
        with Session(engine) as session:
            employees = session.query(Employee).all()
            sport_commuters = [e for e in employees if e.commute_mode in SPORT_COMMUTE_MODES]

            logger.info(
                "Salariés à valider : %d / %d (modes sportifs : %s)",
                len(sport_commuters), len(employees),
                SPORT_COMMUTE_MODES
            )

            for i, emp in enumerate(sport_commuters, 1):
                logger.info(
                    "[%d/%d] %s %s — %s",
                    i, len(sport_commuters),
                    emp.first_name, emp.last_name, emp.commute_mode
                )
                processed += 1

                try:
                    validation = validate_employee_commute(emp)

                    if validation["validation_method"] == "n/a":
                        skipped += 1
                        continue

                    # Supprimer l'ancienne validation si elle existe
                    session.query(CommuteValidation).filter_by(
                        employee_id=emp.employee_id
                    ).delete()

                    cv = CommuteValidation(
                        employee_id=emp.employee_id,
                        home_address=validation["home_address"],
                        company_address=COMPANY_ADDRESS,
                        distance_km=validation["distance_km"],
                        commute_mode=validation["commute_mode"],
                        max_allowed_km=validation["max_allowed_km"],
                        is_valid=validation["is_valid"],
                        anomaly_reason=validation["anomaly_reason"],
                        validation_method=validation["validation_method"],
                    )
                    session.add(cv)

                    if validation["is_valid"] is True:
                        validated += 1
                        logger.info(
                            "  ✅ Valide — %.1f km (max autorisé : %.0f km) [%s]",
                            validation["distance_km"] or 0,
                            validation["max_allowed_km"] or 0,
                            validation["validation_method"]
                        )
                    elif validation["is_valid"] is False:
                        anomalies += 1
                        logger.warning(
                            "  ❌ ANOMALIE — %.1f km (max : %.0f km) — %s",
                            validation["distance_km"],
                            validation["max_allowed_km"],
                            validation["anomaly_reason"]
                        )
                    else:
                        skipped += 1
                        logger.warning("  ⚠️  Validation impossible : %s", validation["anomaly_reason"])

                except Exception as e:
                    errors += 1
                    logger.error("  ❌ Erreur validation %s : %s", emp.employee_id, e)

            # Également valider les non-sportifs (is_valid=True par défaut)
            non_sport_employees = [e for e in employees if e.commute_mode not in SPORT_COMMUTE_MODES]
            for emp in non_sport_employees:
                try:
                    existing = session.query(CommuteValidation).filter_by(
                        employee_id=emp.employee_id
                    ).first()

                    if not existing:
                        cv = CommuteValidation(
                            employee_id=emp.employee_id,
                            home_address=emp.home_address,
                            company_address=COMPANY_ADDRESS,
                            distance_km=None,
                            commute_mode=emp.commute_mode,
                            max_allowed_km=None,
                            is_valid=True,
                            anomaly_reason=None,
                            validation_method="no_check_needed",
                        )
                        session.add(cv)
                        validated += 1

                except Exception as e:
                    errors += 1

            session.commit()

        logger.info("=" * 60)
        logger.info(
            "✅ Validation terminée — Valides: %d | Anomalies: %d | Non traités: %d | Erreurs: %d",
            validated, anomalies, skipped, errors
        )

        # Monitoring
        with Session(engine) as session:
            run = PipelineRun(
                run_id=run_id,
                step_name=step_name,
                status="success" if errors == 0 else "warning",
                records_processed=processed,
                records_inserted=validated + anomalies,
                errors_count=errors,
                warnings_count=anomalies,
                duration_seconds=round((datetime.utcnow() - started_at).total_seconds(), 3),
                details={
                    "sport_commuters": len(sport_commuters),
                    "validated_ok": validated,
                    "anomalies": anomalies,
                    "skipped": skipped,
                    "method": "google_maps" if GOOGLE_MAPS_API_KEY else "haversine",
                },
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()

        return {
            "status": "success",
            "processed": processed,
            "validated": validated,
            "anomalies": anomalies,
            "skipped": skipped,
            "errors": errors,
        }

    except Exception as e:
        logger.error("❌ Validation échouée : %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}


if __name__ == "__main__":
    result = run_validation()
    if result["status"] == "failed":
        sys.exit(1)
