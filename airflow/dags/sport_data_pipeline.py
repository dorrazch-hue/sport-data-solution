"""
DAG Airflow — Sport Data Solution.

Orchestre automatiquement l'ensemble du pipeline de données.
Programmé pour s'exécuter chaque jour à 6h00.

Ordre des tâches :
  ingest_rh → validate_commutes → generate_activities
                                         ↓
                          compute_benefits → quality_tests → slack_notifications

Accès au DAG :
  Airflow UI → http://localhost:8080
  Login : admin / admin
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.models import Variable

# =============================================================
# Configuration du DAG
# =============================================================
default_args = {
    "owner": "sport_data_solution",
    "depends_on_past": False,
    "start_date": datetime(2025, 9, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

dag = DAG(
    dag_id="sport_data_pipeline",
    default_args=default_args,
    description="Pipeline ETL complet — Sport Data Solution (primes sportives & jours bien-être)",
    schedule_interval="0 6 * * *",   # Tous les jours à 6h00
    catchup=False,
    tags=["sport", "rh", "etl", "avantages"],
    max_active_runs=1,               # Un seul run actif à la fois
    doc_md="""
## Pipeline Sport Data Solution

Ce DAG orchestre le pipeline complet de données pour le POC avantages sportifs.

### Étapes
1. **ingest_rh** : Charge les données RH et sportives en base PostgreSQL
2. **validate_commutes** : Valide les trajets domicile-bureau (Google Maps ou Haversine)
3. **generate_activities** : Génère les activités sportives simulées (12 mois)
4. **compute_benefits** : Calcule les avantages (prime 5%, jours bien-être)
5. **quality_tests** : Tests de cohérence et d'intégrité des données
6. **slack_notifications** : Envoie les notifications Slack pour les nouvelles activités

### Paramètres modifiables (Airflow Variables)
- `SPORT_BONUS_RATE` : Taux de la prime sportive (défaut : 0.05)
- `WELLNESS_DAYS_THRESHOLD` : Nombre d'activités minimum (défaut : 15)
- `WELLNESS_DAYS_COUNT` : Jours bien-être accordés (défaut : 5)
    """,
)


# =============================================================
# Fonctions Python pour chaque tâche
# =============================================================

def task_ingest_rh(**context):
    """Ingestion des données RH et sportives."""
    import sys
    sys.path.insert(0, "/opt/airflow")

    from src.ingestion.ingest_rh import run_ingestion
    run_id = context["run_id"]

    result = run_ingestion(run_id=run_id)
    if result["status"] == "failed":
        raise Exception(f"Ingestion RH échouée : {result.get('error', 'unknown')}")

    context["task_instance"].xcom_push(key="ingest_metrics", value=result)
    return result


def task_validate_commutes(**context):
    """Validation des trajets domicile-bureau."""
    import sys
    sys.path.insert(0, "/opt/airflow")

    from src.validation.validate_commute import run_validation
    run_id = context["run_id"]

    result = run_validation(run_id=run_id)
    if result["status"] == "failed":
        raise Exception(f"Validation trajets échouée : {result.get('error', 'unknown')}")

    # Signaler les anomalies dans les logs Airflow
    anomalies = result.get("anomalies", 0)
    if anomalies > 0:
        print(f"⚠️  {anomalies} anomalie(s) de trajet détectée(s) — vérification manuelle recommandée")

    return result


def task_generate_activities(**context):
    """Génération des activités sportives simulées."""
    import sys
    sys.path.insert(0, "/opt/airflow")

    from src.generation.generate_activities import run_generation
    run_id = context["run_id"]

    result = run_generation(run_id=run_id, force_regenerate=False)
    if result["status"] == "failed":
        raise Exception(f"Génération activités échouée : {result.get('error', 'unknown')}")

    return result


def task_compute_benefits(**context):
    """Calcul des avantages salariaux."""
    import sys
    sys.path.insert(0, "/opt/airflow")

    from src.transformation.compute_benefits import run_computation
    run_id = context["run_id"]

    # Récupérer l'année depuis le contexte d'exécution
    execution_date = context["data_interval_start"]
    year = execution_date.year

    result = run_computation(run_id=run_id, year=year)
    if result["status"] == "failed":
        raise Exception(f"Calcul avantages échoué : {result.get('error', 'unknown')}")

    # Pousser les métriques pour le monitoring Airflow
    context["task_instance"].xcom_push(key="benefits_summary", value={
        "year": year,
        "eligible_bonus": result.get("eligible_bonus", 0),
        "eligible_wellness": result.get("eligible_wellness", 0),
        "total_cost_eur": result.get("total_cost_eur", 0),
        "commute_anomalies": result.get("commute_anomalies", 0),
    })

    return result


def task_quality_tests(**context):
    """Tests de qualité des données."""
    import sys
    sys.path.insert(0, "/opt/airflow")

    from src.quality.data_quality import run_quality_tests
    run_id = context["run_id"]

    result = run_quality_tests(run_id=run_id)

    # Échouer le DAG si des tests critiques échouent
    if result.get("failed_errors", 0) > 0:
        raise Exception(
            f"Tests de qualité : {result['failed_errors']} erreur(s) critique(s) détectée(s). "
            f"Consulter les logs pour le détail."
        )

    # Avertissement en cas de warnings
    if result.get("warnings", 0) > 0:
        print(f"⚠️  {result['warnings']} avertissement(s) — contrôle manuel recommandé")

    return result


def task_slack_notifications(**context):
    """Envoi des notifications Slack pour les nouvelles activités."""
    import sys
    sys.path.insert(0, "/opt/airflow")

    from src.notifications.slack_notifier import notify_pending_activities
    run_id = context["run_id"]

    result = notify_pending_activities(run_id=run_id, batch_size=100)
    if result["status"] == "failed":
        raise Exception(f"Notifications Slack échouées : {result.get('error', 'unknown')}")

    return result


# =============================================================
# Définition des tâches et des dépendances
# =============================================================

with dag:

    start = EmptyOperator(task_id="start")

    ingest = PythonOperator(
        task_id="ingest_rh",
        python_callable=task_ingest_rh,
    )

    validate = PythonOperator(
        task_id="validate_commutes",
        python_callable=task_validate_commutes,
    )

    generate = PythonOperator(
        task_id="generate_activities",
        python_callable=task_generate_activities,
    )

    compute = PythonOperator(
        task_id="compute_benefits",
        python_callable=task_compute_benefits,
    )

    quality = PythonOperator(
        task_id="quality_tests",
        python_callable=task_quality_tests,
    )

    notify = PythonOperator(
        task_id="slack_notifications",
        python_callable=task_slack_notifications,
    )

    end = EmptyOperator(task_id="end")

    # ──────────────────────────────────────────
    # Chaîne de dépendances
    # ──────────────────────────────────────────
    #
    #         ingest_rh
    #         /       \
    #    validate   generate
    #         \       /
    #          compute
    #             |
    #         quality_tests
    #             |
    #       slack_notifications
    #             |
    #            end
    #
    start >> ingest
    ingest >> [validate, generate]
    [validate, generate] >> compute
    compute >> quality
    quality >> notify
    notify >> end
