"""
Tests de qualité des données — Sport Data Solution.
Utilise Great Expectations pour valider l'intégrité et la cohérence des données.

Tests couverts :
  Table employees :
    - employee_id unique et non null
    - gross_salary > 0
    - commute_mode dans les valeurs autorisées
    - Pas de nom ou prénom vide

  Table sport_activities :
    - distance_meters >= 0 (ou null pour sports sans distance)
    - duration_seconds > 0
    - activity_start dans la plage des 12 derniers mois
    - activity_start < activity_end
    - sport_type non null et dans les types connus

  Table benefits_calculations :
    - sport_bonus_amount = gross_salary * 0.05 si éligible
    - wellness_days_count = 5 si éligible, 0 sinon
    - activity_count_year >= 15 si éligible jours bien-être

  Table commute_validations :
    - distance_km >= 0 pour les modes sportifs
    - is_valid non null pour les modes sportifs

Usage : python -m src.quality.data_quality
"""

import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.database.init_db import get_engine
from src.database.models import PipelineRun
from src.config import (
    SPORT_BONUS_RATE, WELLNESS_DAYS_THRESHOLD, WELLNESS_DAYS_COUNT,
    SPORT_COMMUTE_MODES, SPORT_PROFILES
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("data_quality")


# ============================================================
# Valeurs de référence pour la validation
# ============================================================

VALID_COMMUTE_MODES = [
    "Marche/running",
    "Vélo/Trottinette/Autres",
    "Transports en commun",
    "véhicule thermique/électrique",
]

VALID_CONTRACT_TYPES = ["CDI", "CDD"]

VALID_BUSINESS_UNITS = ["Finance", "Support", "Ventes", "R&D", "Marketing"]

VALID_SPORT_TYPES = list(SPORT_PROFILES.keys())

# Période de génération des activités
ACTIVITY_PERIOD_START = datetime.utcnow() - timedelta(days=366)
ACTIVITY_PERIOD_END = datetime.utcnow() + timedelta(hours=1)


class TestResult:
    """Résultat d'un test de qualité."""
    def __init__(self, name: str, passed: bool, details: str = "",
                 severity: str = "ERROR", count_failed: int = 0):
        self.name = name
        self.passed = passed
        self.details = details
        self.severity = severity  # "ERROR" | "WARNING" | "INFO"
        self.count_failed = count_failed

    def __repr__(self):
        icon = "✅" if self.passed else ("⚠️" if self.severity == "WARNING" else "❌")
        base = f"{icon} [{self.severity}] {self.name}"
        if not self.passed:
            base += f" — {self.details}"
            if self.count_failed > 0:
                base += f" ({self.count_failed} cas)"
        return base


class DataQualitySuite:
    """Suite de tests de qualité des données."""

    def __init__(self, engine):
        self.engine = engine
        self.results: list[TestResult] = []

    def _query_df(self, sql: str) -> pd.DataFrame:
        """Exécute une requête SQL et retourne un DataFrame."""
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn)

    def add_result(self, result: TestResult):
        self.results.append(result)
        logger.info(str(result))

    # ============================================================
    # Tests table : employees
    # ============================================================

    def test_employee_ids_unique(self):
        """Vérifie que tous les IDs salariés sont uniques."""
        df = self._query_df(
            "SELECT employee_id, COUNT(*) as cnt "
            "FROM employees GROUP BY employee_id HAVING COUNT(*) > 1"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="employees.employee_id — Unicité",
            passed=passed,
            details=f"{len(df)} ID(s) en doublon" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_employee_salary_positive(self):
        """Vérifie que tous les salaires sont strictement positifs."""
        df = self._query_df(
            "SELECT employee_id, gross_salary FROM employees WHERE gross_salary <= 0 OR gross_salary IS NULL"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="employees.gross_salary — Valeur positive",
            passed=passed,
            details=f"{len(df)} salarié(s) avec salaire invalide" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_employee_commute_mode_valid(self):
        """Vérifie que tous les modes de déplacement sont dans la liste autorisée."""
        modes_str = ", ".join(f"'{m}'" for m in VALID_COMMUTE_MODES)
        df = self._query_df(
            f"SELECT employee_id, commute_mode FROM employees "
            f"WHERE commute_mode NOT IN ({modes_str}) AND commute_mode IS NOT NULL"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="employees.commute_mode — Valeurs autorisées",
            passed=passed,
            details=f"{len(df)} mode(s) inconnu(s) : {df['commute_mode'].unique().tolist() if not passed else ''}" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_employee_names_not_empty(self):
        """Vérifie qu'aucun nom ou prénom n'est vide."""
        df = self._query_df(
            "SELECT employee_id FROM employees "
            "WHERE last_name IS NULL OR last_name = '' OR first_name IS NULL OR first_name = ''"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="employees.last_name/first_name — Non vides",
            passed=passed,
            details=f"{len(df)} salarié(s) sans nom complet" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_employee_contract_type_valid(self):
        """Vérifie que les types de contrat sont CDI ou CDD."""
        types_str = ", ".join(f"'{t}'" for t in VALID_CONTRACT_TYPES)
        df = self._query_df(
            f"SELECT employee_id, contract_type FROM employees "
            f"WHERE contract_type NOT IN ({types_str}) AND contract_type IS NOT NULL"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="employees.contract_type — CDI/CDD seulement",
            passed=passed,
            details=f"{len(df)} type(s) de contrat invalide(s)" if not passed else "",
            severity="WARNING",
            count_failed=len(df)
        ))

    def test_employee_hire_date_reasonable(self):
        """Vérifie que les dates d'embauche sont dans une plage raisonnable."""
        df = self._query_df(
            "SELECT employee_id, hire_date FROM employees "
            "WHERE hire_date < '1990-01-01' OR hire_date > CURRENT_DATE"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="employees.hire_date — Date raisonnable (1990 → aujourd'hui)",
            passed=passed,
            details=f"{len(df)} date(s) d'embauche suspectes" if not passed else "",
            severity="WARNING",
            count_failed=len(df)
        ))

    # ============================================================
    # Tests table : sport_activities
    # ============================================================

    def test_activities_distance_non_negative(self):
        """Vérifie que les distances ne sont pas négatives."""
        df = self._query_df(
            "SELECT id, employee_id, distance_meters FROM sport_activities "
            "WHERE distance_meters < 0"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.distance_meters — Non négative",
            passed=passed,
            details=f"{len(df)} activité(s) avec distance négative" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_activities_duration_positive(self):
        """Vérifie que toutes les durées sont strictement positives."""
        df = self._query_df(
            "SELECT id, employee_id, duration_seconds FROM sport_activities "
            "WHERE duration_seconds IS NULL OR duration_seconds <= 0"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.duration_seconds — Strictement positive",
            passed=passed,
            details=f"{len(df)} activité(s) avec durée invalide" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_activities_date_in_valid_range(self):
        """Vérifie que les dates d'activité sont dans la période de simulation."""
        start_str = ACTIVITY_PERIOD_START.strftime("%Y-%m-%d")
        df = self._query_df(
            f"SELECT id, activity_start FROM sport_activities "
            f"WHERE activity_start < '{start_str}' OR activity_start > NOW()"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.activity_start — Dans la période valide (12 mois)",
            passed=passed,
            details=f"{len(df)} activité(s) hors période" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_activities_end_after_start(self):
        """Vérifie que la date de fin est toujours après la date de début."""
        df = self._query_df(
            "SELECT id FROM sport_activities "
            "WHERE activity_end IS NOT NULL AND activity_end <= activity_start"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.activity_end > activity_start",
            passed=passed,
            details=f"{len(df)} activité(s) avec dates incohérentes" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_activities_sport_type_not_null(self):
        """Vérifie qu'aucune activité n'a un type de sport null."""
        df = self._query_df(
            "SELECT id FROM sport_activities WHERE sport_type IS NULL OR sport_type = ''"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.sport_type — Non null/vide",
            passed=passed,
            details=f"{len(df)} activité(s) sans type de sport" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_activities_employee_exists(self):
        """Vérifie que toutes les activités sont liées à un salarié existant."""
        df = self._query_df(
            "SELECT sa.id, sa.employee_id FROM sport_activities sa "
            "LEFT JOIN employees e ON e.employee_id = sa.employee_id "
            "WHERE e.id IS NULL"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.employee_id — Clé étrangère valide",
            passed=passed,
            details=f"{len(df)} activité(s) orphelines" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_activities_no_future_dates(self):
        """Vérifie qu'aucune activité n'est dans le futur."""
        df = self._query_df(
            "SELECT id, activity_start FROM sport_activities "
            "WHERE activity_start > NOW() + INTERVAL '1 hour'"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="sport_activities.activity_start — Pas de dates futures",
            passed=passed,
            details=f"{len(df)} activité(s) dans le futur" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    # ============================================================
    # Tests table : benefits_calculations
    # ============================================================

    def test_benefits_bonus_amount_correct(self):
        """Vérifie que le montant de la prime = salaire * SPORT_BONUS_RATE pour les éligibles."""
        tolerance = 0.01  # Tolérance d'arrondi de 1 centime
        df = self._query_df(
            f"""
            SELECT bc.employee_id, bc.sport_bonus_amount, e.gross_salary,
                   ABS(bc.sport_bonus_amount - (e.gross_salary * {SPORT_BONUS_RATE})) as diff
            FROM benefits_calculations bc
            JOIN employees e ON e.employee_id = bc.employee_id
            WHERE bc.eligible_sport_bonus = TRUE
              AND ABS(bc.sport_bonus_amount - (e.gross_salary * {SPORT_BONUS_RATE})) > {tolerance}
            """
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name=f"benefits.sport_bonus_amount — Cohérence ({SPORT_BONUS_RATE*100:.0f}% du salaire)",
            passed=passed,
            details=f"{len(df)} montant(s) de prime incorrect(s)" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_benefits_wellness_days_correct(self):
        """Vérifie que les jours bien-être sont 5 si éligible, 0 sinon."""
        df = self._query_df(
            f"""
            SELECT employee_id, eligible_wellness_days, wellness_days_count
            FROM benefits_calculations
            WHERE (eligible_wellness_days = TRUE AND wellness_days_count != {WELLNESS_DAYS_COUNT})
               OR (eligible_wellness_days = FALSE AND wellness_days_count != 0)
            """
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name=f"benefits.wellness_days_count — {WELLNESS_DAYS_COUNT}j si éligible, 0 sinon",
            passed=passed,
            details=f"{len(df)} calcul(s) de jours bien-être incorrect(s)" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_benefits_activity_count_vs_eligibility(self):
        """Vérifie la cohérence entre le nombre d'activités et l'éligibilité jours bien-être."""
        df = self._query_df(
            f"""
            SELECT employee_id, activity_count_year, eligible_wellness_days
            FROM benefits_calculations
            WHERE (eligible_wellness_days = TRUE AND activity_count_year < {WELLNESS_DAYS_THRESHOLD})
               OR (eligible_wellness_days = FALSE AND activity_count_year >= {WELLNESS_DAYS_THRESHOLD}
                   )
            """
        )
        # Jointure sur employees pour vérifier declared_sport
        df2 = self._query_df(
            f"""
            SELECT bc.employee_id, bc.activity_count_year, bc.eligible_wellness_days, e.declared_sport
            FROM benefits_calculations bc
            JOIN employees e ON e.employee_id = bc.employee_id
            WHERE (bc.eligible_wellness_days = TRUE AND bc.activity_count_year < {WELLNESS_DAYS_THRESHOLD})
               OR (bc.eligible_wellness_days = FALSE AND bc.activity_count_year >= {WELLNESS_DAYS_THRESHOLD}
                   AND e.declared_sport IS NOT NULL)
            """
        )
        passed = len(df2) == 0
        self.add_result(TestResult(
            name=f"benefits.eligible_wellness — Cohérence avec activity_count (seuil={WELLNESS_DAYS_THRESHOLD})",
            passed=passed,
            details=f"{len(df2)} incohérence(s) éligibilité/activités" if not passed else "",
            severity="ERROR",
            count_failed=len(df2)
        ))

    def test_benefits_cost_non_negative(self):
        """Vérifie que le coût entreprise n'est jamais négatif."""
        df = self._query_df(
            "SELECT employee_id, total_cost_company FROM benefits_calculations "
            "WHERE total_cost_company < 0"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="benefits.total_cost_company — Non négatif",
            passed=passed,
            details=f"{len(df)} coût(s) négatif(s)" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    # ============================================================
    # Tests table : commute_validations
    # ============================================================

    def test_commute_distance_non_negative(self):
        """Vérifie que les distances ne sont pas négatives."""
        df = self._query_df(
            "SELECT employee_id, distance_km FROM commute_validations WHERE distance_km < 0"
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="commute_validations.distance_km — Non négative",
            passed=passed,
            details=f"{len(df)} distance(s) négative(s)" if not passed else "",
            severity="ERROR",
            count_failed=len(df)
        ))

    def test_commute_sport_mode_has_validation(self):
        """Vérifie que tous les salariés avec mode sportif ont une validation."""
        modes_str = ", ".join(f"'{m}'" for m in SPORT_COMMUTE_MODES)
        df = self._query_df(
            f"""
            SELECT e.employee_id, e.commute_mode
            FROM employees e
            LEFT JOIN commute_validations cv ON cv.employee_id = e.employee_id
            WHERE e.commute_mode IN ({modes_str})
              AND cv.id IS NULL
            """
        )
        passed = len(df) == 0
        self.add_result(TestResult(
            name="commute_validations — Tous les modes sportifs validés",
            passed=passed,
            details=f"{len(df)} salarié(s) sportif(s) sans validation de trajet" if not passed else "",
            severity="WARNING",
            count_failed=len(df)
        ))

    # ============================================================
    # Rapport final
    # ============================================================

    def run_all_tests(self) -> dict:
        """Exécute tous les tests et retourne un rapport."""
        logger.info("=" * 60)
        logger.info("ÉTAPE : Tests de qualité des données (Great Expectations)")
        logger.info("=" * 60)

        test_groups = [
            ("employees", [
                self.test_employee_ids_unique,
                self.test_employee_salary_positive,
                self.test_employee_commute_mode_valid,
                self.test_employee_names_not_empty,
                self.test_employee_contract_type_valid,
                self.test_employee_hire_date_reasonable,
            ]),
            ("sport_activities", [
                self.test_activities_distance_non_negative,
                self.test_activities_duration_positive,
                self.test_activities_date_in_valid_range,
                self.test_activities_end_after_start,
                self.test_activities_sport_type_not_null,
                self.test_activities_employee_exists,
                self.test_activities_no_future_dates,
            ]),
            ("benefits_calculations", [
                self.test_benefits_bonus_amount_correct,
                self.test_benefits_wellness_days_correct,
                self.test_benefits_activity_count_vs_eligibility,
                self.test_benefits_cost_non_negative,
            ]),
            ("commute_validations", [
                self.test_commute_distance_non_negative,
                self.test_commute_sport_mode_has_validation,
            ]),
        ]

        for group_name, tests in test_groups:
            logger.info("")
            logger.info("--- %s ---", group_name.upper())
            for test_fn in tests:
                try:
                    test_fn()
                except Exception as e:
                    logger.error("  ❌ Erreur lors du test %s : %s", test_fn.__name__, e)
                    self.add_result(TestResult(
                        name=test_fn.__name__,
                        passed=False,
                        details=f"Exception : {e}",
                        severity="ERROR"
                    ))

        # Résumé
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed_errors = sum(1 for r in self.results if not r.passed and r.severity == "ERROR")
        warnings = sum(1 for r in self.results if not r.passed and r.severity == "WARNING")

        logger.info("")
        logger.info("=" * 60)
        logger.info("RÉSUMÉ QUALITÉ DONNÉES")
        logger.info("=" * 60)
        logger.info("Tests réussis   : %d / %d", passed, total)
        logger.info("Erreurs (ERROR) : %d", failed_errors)
        logger.info("Avertissements  : %d", warnings)

        overall_status = "passed" if failed_errors == 0 else "failed"
        logger.info("Statut global   : %s", "✅ SUCCÈS" if overall_status == "passed" else "❌ ÉCHEC")
        logger.info("=" * 60)

        return {
            "total_tests": total,
            "passed": passed,
            "failed_errors": failed_errors,
            "warnings": warnings,
            "overall_status": overall_status,
        }


def run_quality_tests(run_id: str = None) -> dict:
    """Point d'entrée principal — exécute tous les tests de qualité."""
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    started_at = datetime.utcnow()
    step_name = "data_quality_tests"

    engine = get_engine()
    suite = DataQualitySuite(engine)

    report = suite.run_all_tests()

    # Monitoring
    with Session(engine) as session:
        run = PipelineRun(
            run_id=run_id,
            step_name=step_name,
            status="success" if report["overall_status"] == "passed" else "failed",
            records_processed=report["total_tests"],
            records_inserted=0,
            errors_count=report["failed_errors"],
            warnings_count=report["warnings"],
            duration_seconds=round((datetime.utcnow() - started_at).total_seconds(), 3),
            details={
                "total_tests": report["total_tests"],
                "passed": report["passed"],
                "failed_errors": report["failed_errors"],
                "warnings": report["warnings"],
                "overall_status": report["overall_status"],
            },
            started_at=started_at,
            completed_at=datetime.utcnow(),
        )
        session.add(run)
        session.commit()

    return {"status": report["overall_status"], **report}


if __name__ == "__main__":
    result = run_quality_tests()
    if result["status"] == "failed":
        sys.exit(1)
