"""
Ingestion des données RH et sportives.

Étapes :
  1. Lecture des fichiers Excel (donnees_rh.xlsx, donnees_sportives.xlsx)
  2. Nettoyage et normalisation des données
     - Correction de la faute de frappe "Runing" → "Running"
     - Normalisation des modes de déplacement
  3. Jointure RH + sports
  4. Chargement en base PostgreSQL (table employees)
  5. Enregistrement du run dans pipeline_runs

Usage : python -m src.ingestion.ingest_rh
"""

import sys
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.config import RH_FILE, SPORT_FILE
from src.database.init_db import get_engine
from src.database.models import Employee, PipelineRun

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("ingest_rh")


# =============================================================
# Constantes de normalisation
# =============================================================

# Correction des fautes dans les données sources
SPORT_CORRECTIONS = {
    "Runing": "Running",   # Faute de frappe dans le fichier source
    "runing": "Running",
    "running": "Running",
    "course à pied": "Course à pied",
}

# Normalisation des modes de déplacement
COMMUTE_MODE_NORMALIZATION = {
    "marche/running": "Marche/running",
    "Marche/Running": "Marche/running",
    "vélo/trottinette/autres": "Vélo/Trottinette/Autres",
    "Vélo/trottinette/autres": "Vélo/Trottinette/Autres",
    "véhicule thermique/électrique": "véhicule thermique/électrique",
    "Véhicule thermique/électrique": "véhicule thermique/électrique",
}


def read_rh_file(filepath: Path) -> pd.DataFrame:
    """Lit et valide le fichier Excel des données RH."""
    logger.info("Lecture du fichier RH : %s", filepath)

    if not filepath.exists():
        raise FileNotFoundError(f"Fichier RH introuvable : {filepath}")

    df = pd.read_excel(filepath, engine="openpyxl")
    logger.info("  → %d lignes chargées, colonnes : %s", len(df), list(df.columns))

    # Vérifier les colonnes attendues
    expected_cols = [
        "ID salarié", "Nom", "Prénom", "Date de naissance", "BU",
        "Date d'embauche", "Salaire brut", "Type de contrat",
        "Nombre de jours de CP", "Adresse du domicile", "Moyen de déplacement"
    ]
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes manquantes dans le fichier RH : {missing}")

    return df


def read_sport_file(filepath: Path) -> pd.DataFrame:
    """Lit et valide le fichier Excel des données sportives."""
    logger.info("Lecture du fichier Sportif : %s", filepath)

    if not filepath.exists():
        raise FileNotFoundError(f"Fichier Sportif introuvable : {filepath}")

    df = pd.read_excel(filepath, engine="openpyxl")
    logger.info("  → %d lignes chargées, colonnes : %s", len(df), list(df.columns))

    expected_cols = ["ID salarié", "Pratique d'un sport"]
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Colonnes manquantes dans le fichier Sportif : {missing}")

    return df


def clean_rh_data(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoie et normalise les données RH."""
    logger.info("Nettoyage des données RH...")
    df = df.copy()

    # Supprimer les espaces superflus dans les colonnes texte
    for col in ["Nom", "Prénom", "BU", "Type de contrat", "Adresse du domicile", "Moyen de déplacement"]:
        df[col] = df[col].astype(str).str.strip()

    # Normaliser les modes de déplacement
    original_modes = df["Moyen de déplacement"].unique()
    df["Moyen de déplacement"] = df["Moyen de déplacement"].replace(COMMUTE_MODE_NORMALIZATION)

    normalized_modes = df["Moyen de déplacement"].unique()
    if set(original_modes) != set(normalized_modes):
        logger.info("  → Modes de déplacement normalisés : %s", dict(zip(original_modes, normalized_modes)))

    # Convertir les dates
    df["Date de naissance"] = pd.to_datetime(df["Date de naissance"], errors="coerce")
    df["Date d'embauche"] = pd.to_datetime(df["Date d'embauche"], errors="coerce")

    # Vérification des doublons
    dupes = df.duplicated(subset=["ID salarié"]).sum()
    if dupes > 0:
        logger.warning("  ⚠️  %d IDs salariés en doublon détectés — conservation du premier", dupes)
        df = df.drop_duplicates(subset=["ID salarié"], keep="first")

    # Vérification des valeurs nulles critiques
    null_salary = df["Salaire brut"].isna().sum()
    if null_salary > 0:
        logger.warning("  ⚠️  %d salaires manquants", null_salary)

    logger.info("  ✅ %d salariés après nettoyage", len(df))
    return df


def clean_sport_data(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoie et normalise les données sportives."""
    logger.info("Nettoyage des données sportives...")
    df = df.copy()

    # Corriger la faute de frappe "Runing" → "Running"
    df["Pratique d'un sport"] = df["Pratique d'un sport"].replace(SPORT_CORRECTIONS)

    # Détecter et logger les corrections
    runing_count = (df["Pratique d'un sport"] == "Running").sum()
    if runing_count > 0:
        logger.info(
            "  ✏️  %d occurrence(s) de 'Runing' corrigées en 'Running'",
            runing_count
        )

    # Stats
    with_sport = df["Pratique d'un sport"].notna().sum()
    without_sport = df["Pratique d'un sport"].isna().sum()
    logger.info(
        "  → %d salariés avec sport déclaré, %d sans sport",
        with_sport, without_sport
    )

    return df


def merge_rh_sport(df_rh: pd.DataFrame, df_sport: pd.DataFrame) -> pd.DataFrame:
    """Joint les données RH et sportives sur l'ID salarié."""
    logger.info("Jointure RH + Données sportives...")

    df_merged = df_rh.merge(
        df_sport[["ID salarié", "Pratique d'un sport"]],
        on="ID salarié",
        how="left"
    )

    logger.info(
        "  → %d salariés après jointure (attendu : %d)",
        len(df_merged), len(df_rh)
    )

    if len(df_merged) != len(df_rh):
        logger.warning(
            "  ⚠️  Divergence entre RH (%d) et résultat jointure (%d)",
            len(df_rh), len(df_merged)
        )

    return df_merged


def df_to_employees(df: pd.DataFrame) -> list[Employee]:
    """Convertit un DataFrame en liste d'objets SQLAlchemy Employee."""
    employees = []
    for _, row in df.iterrows():
        emp = Employee(
            employee_id=int(row["ID salarié"]),
            last_name=str(row["Nom"]).strip(),
            first_name=str(row["Prénom"]).strip(),
            birth_date=row["Date de naissance"].date() if pd.notna(row["Date de naissance"]) else None,
            business_unit=str(row["BU"]).strip() if pd.notna(row["BU"]) else None,
            hire_date=row["Date d'embauche"].date() if pd.notna(row["Date d'embauche"]) else None,
            gross_salary=float(row["Salaire brut"]),
            contract_type=str(row["Type de contrat"]).strip() if pd.notna(row["Type de contrat"]) else None,
            vacation_days=int(row["Nombre de jours de CP"]) if pd.notna(row["Nombre de jours de CP"]) else None,
            home_address=str(row["Adresse du domicile"]).strip() if pd.notna(row["Adresse du domicile"]) else None,
            commute_mode=str(row["Moyen de déplacement"]).strip() if pd.notna(row["Moyen de déplacement"]) else None,
            declared_sport=(
                str(row["Pratique d'un sport"]).strip()
                if pd.notna(row.get("Pratique d'un sport"))
                else None
            ),
        )
        employees.append(emp)
    return employees


def load_to_database(employees: list[Employee], engine) -> dict:
    """
    Charge les employés en base de données.
    Stratégie : UPSERT — met à jour si l'ID existe déjà, insère sinon.
    """
    logger.info("Chargement en base de données (%d salariés)...", len(employees))

    inserted = 0
    updated = 0
    errors = 0

    with Session(engine) as session:
        for emp in employees:
            try:
                # Rechercher par employee_id (clé métier, pas PK autoincrément)
                existing = session.query(Employee).filter_by(
                    employee_id=emp.employee_id
                ).first()

                if existing:
                    # Mise à jour des champs
                    existing.last_name = emp.last_name
                    existing.first_name = emp.first_name
                    existing.birth_date = emp.birth_date
                    existing.business_unit = emp.business_unit
                    existing.hire_date = emp.hire_date
                    existing.gross_salary = emp.gross_salary
                    existing.contract_type = emp.contract_type
                    existing.vacation_days = emp.vacation_days
                    existing.home_address = emp.home_address
                    existing.commute_mode = emp.commute_mode
                    existing.declared_sport = emp.declared_sport
                    existing.updated_at = datetime.utcnow()
                    updated += 1
                else:
                    session.add(emp)
                    inserted += 1

            except Exception as e:
                logger.error("  ❌ Erreur salarié %s : %s", emp.employee_id, e)
                errors += 1

        session.commit()

    logger.info("  ✅ %d insérés, %d mis à jour, %d erreurs", inserted, updated, errors)
    return {"inserted": inserted, "updated": updated, "errors": errors}


def log_pipeline_run(engine, run_id: str, step_name: str, status: str,
                     started_at: datetime, records_processed: int,
                     records_inserted: int, errors_count: int,
                     details: dict = None, error_message: str = None) -> None:
    """Enregistre le résultat de cette étape dans la table pipeline_runs."""
    completed_at = datetime.utcnow()
    duration = (completed_at - started_at).total_seconds()

    with Session(engine) as session:
        run = PipelineRun(
            run_id=run_id,
            step_name=step_name,
            status=status,
            records_processed=records_processed,
            records_inserted=records_inserted,
            errors_count=errors_count,
            duration_seconds=round(duration, 3),
            details=details or {},
            error_message=error_message,
            started_at=started_at,
            completed_at=completed_at,
        )
        session.add(run)
        session.commit()

    logger.info(
        "  📊 Pipeline run enregistré — %s | %s | %.2fs",
        step_name, status, duration
    )


def run_ingestion(run_id: str = None) -> dict:
    """
    Exécute la pipeline complète d'ingestion.
    Retourne un dict avec les métriques d'exécution.
    """
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    started_at = datetime.utcnow()
    step_name = "ingest_rh"

    logger.info("=" * 60)
    logger.info("ÉTAPE : Ingestion données RH et Sportives")
    logger.info("Run ID : %s", run_id)
    logger.info("=" * 60)

    engine = get_engine()

    try:
        # 1. Lecture des fichiers
        df_rh = read_rh_file(RH_FILE)
        df_sport = read_sport_file(SPORT_FILE)

        # 2. Nettoyage
        df_rh_clean = clean_rh_data(df_rh)
        df_sport_clean = clean_sport_data(df_sport)

        # 3. Jointure
        df_final = merge_rh_sport(df_rh_clean, df_sport_clean)

        # 4. Conversion en objets SQLAlchemy
        employees = df_to_employees(df_final)

        # 5. Chargement en base
        metrics = load_to_database(employees, engine)

        # 6. Logging monitoring
        log_pipeline_run(
            engine=engine,
            run_id=run_id,
            step_name=step_name,
            status="success",
            started_at=started_at,
            records_processed=len(employees),
            records_inserted=metrics["inserted"],
            errors_count=metrics["errors"],
            details={
                "rh_rows": len(df_rh),
                "sport_rows": len(df_sport),
                "employees_with_sport": df_final["Pratique d'un sport"].notna().sum(),
                "employees_sport_commute": df_final["Moyen de déplacement"].isin(
                    ["Marche/running", "Vélo/Trottinette/Autres"]
                ).sum(),
            }
        )

        logger.info("✅ Ingestion terminée avec succès")
        return {"status": "success", **metrics}

    except Exception as e:
        logger.error("❌ Ingestion échouée : %s", e, exc_info=True)
        try:
            log_pipeline_run(
                engine=engine,
                run_id=run_id,
                step_name=step_name,
                status="failed",
                started_at=started_at,
                records_processed=0,
                records_inserted=0,
                errors_count=1,
                error_message=str(e)
            )
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}


if __name__ == "__main__":
    result = run_ingestion()
    if result["status"] == "failed":
        sys.exit(1)
