"""
Pipeline principal — Sport Data Solution.

Lance toutes les étapes dans l'ordre correct :
  1. Initialisation de la base de données
  2. Ingestion des données RH et sportives
  3. Validation des trajets domicile-bureau
  4. Génération des activités sportives simulées
  5. Calcul des avantages salariaux
  6. Tests de qualité des données
  7. Envoi des notifications Slack

Usage :
  # Pipeline complet
  python pipeline/run_pipeline.py

  # Regénérer les activités (repart de zéro)
  python pipeline/run_pipeline.py --force-regenerate

  # Sauter une étape
  python pipeline/run_pipeline.py --skip-validation

  # Uniquement les tests de qualité
  python pipeline/run_pipeline.py --only quality
"""

import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.init_db import init_db
from src.ingestion.ingest_rh import run_ingestion
from src.validation.validate_commute import run_validation
from src.generation.generate_activities import run_generation
from src.transformation.compute_benefits import run_computation
from src.quality.data_quality import run_quality_tests
from src.notifications.slack_notifier import notify_pending_activities

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent.parent / "logs" / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        ),
    ]
)
logger = logging.getLogger("pipeline")

STEPS = ["init_db", "ingest", "validate", "generate", "compute", "quality", "notify"]


def run_step(name: str, fn, **kwargs) -> dict:
    """Exécute une étape du pipeline avec gestion des erreurs."""
    logger.info("")
    logger.info("▶ " + "=" * 55)
    logger.info("▶ DÉBUT ÉTAPE : %s", name.upper())
    logger.info("▶ " + "=" * 55)

    start = datetime.utcnow()
    try:
        result = fn(**kwargs)
        elapsed = (datetime.utcnow() - start).total_seconds()

        status = result.get("status", "unknown") if isinstance(result, dict) else "success"

        if status in ("success", "skipped"):
            logger.info("▶ ✅ ÉTAPE '%s' terminée en %.2fs (%s)", name, elapsed, status)
        else:
            logger.error("▶ ❌ ÉTAPE '%s' ÉCHOUÉE en %.2fs", name, elapsed)

        return {"step": name, "status": status, "elapsed": elapsed, **(result if isinstance(result, dict) else {})}

    except Exception as e:
        elapsed = (datetime.utcnow() - start).total_seconds()
        logger.error("▶ ❌ ÉTAPE '%s' EXCEPTION : %s", name, e, exc_info=True)
        return {"step": name, "status": "failed", "error": str(e), "elapsed": elapsed}


def run_full_pipeline(
    skip_steps: list[str] = None,
    only_step: str = None,
    force_regenerate: bool = False,
    run_id: str = None,
) -> dict:
    """
    Exécute le pipeline complet dans l'ordre.
    Retourne un rapport avec le statut de chaque étape.
    """
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    skip_steps = skip_steps or []
    pipeline_start = datetime.utcnow()

    logger.info("")
    logger.info("╔" + "═" * 57 + "╗")
    logger.info("║   SPORT DATA SOLUTION — PIPELINE PRINCIPAL              ║")
    logger.info("║   Run ID : %-44s ║" % run_id)
    logger.info("╚" + "═" * 57 + "╝")

    step_results = []

    def should_run(step_name: str) -> bool:
        if only_step and step_name != only_step:
            return False
        if step_name in skip_steps:
            logger.info("⏭️  Étape '%s' ignorée (--skip-%s)", step_name, step_name)
            return False
        return True

    # ──────────────────────────────────────────────────────────────
    # 1. Initialisation de la base de données
    # ──────────────────────────────────────────────────────────────
    if should_run("init_db"):
        result = run_step("init_db", init_db)
        step_results.append(result)
        if result["status"] == "failed":
            logger.error("❌ Arrêt du pipeline — impossible d'initialiser la base")
            return _build_report(run_id, pipeline_start, step_results, "failed")

    # ──────────────────────────────────────────────────────────────
    # 2. Ingestion des données RH et sportives
    # ──────────────────────────────────────────────────────────────
    if should_run("ingest"):
        result = run_step("ingest", run_ingestion, run_id=run_id)
        step_results.append(result)
        if result["status"] == "failed":
            logger.error("❌ Arrêt du pipeline — ingestion RH échouée")
            return _build_report(run_id, pipeline_start, step_results, "failed")

    # ──────────────────────────────────────────────────────────────
    # 3. Validation des trajets domicile-bureau
    # ──────────────────────────────────────────────────────────────
    if should_run("validate"):
        result = run_step("validate", run_validation, run_id=run_id)
        step_results.append(result)
        # La validation peut avoir des avertissements mais n'arrête pas le pipeline

    # ──────────────────────────────────────────────────────────────
    # 4. Génération des activités sportives
    # ──────────────────────────────────────────────────────────────
    if should_run("generate"):
        result = run_step(
            "generate",
            run_generation,
            run_id=run_id,
            force_regenerate=force_regenerate
        )
        step_results.append(result)
        if result["status"] == "failed":
            logger.error("❌ Arrêt du pipeline — génération activités échouée")
            return _build_report(run_id, pipeline_start, step_results, "failed")

    # ──────────────────────────────────────────────────────────────
    # 5. Calcul des avantages salariaux
    # ──────────────────────────────────────────────────────────────
    if should_run("compute"):
        result = run_step("compute", run_computation, run_id=run_id)
        step_results.append(result)
        if result["status"] == "failed":
            logger.error("❌ Arrêt du pipeline — calcul avantages échoué")
            return _build_report(run_id, pipeline_start, step_results, "failed")

    # ──────────────────────────────────────────────────────────────
    # 6. Tests de qualité des données
    # ──────────────────────────────────────────────────────────────
    if should_run("quality"):
        result = run_step("quality", run_quality_tests, run_id=run_id)
        step_results.append(result)
        if result.get("failed_errors", 0) > 0:
            logger.warning(
                "⚠️  %d test(s) de qualité en ERREUR — vérifier les données",
                result["failed_errors"]
            )
            # On continue le pipeline mais on signale l'anomalie

    # ──────────────────────────────────────────────────────────────
    # 7. Envoi des notifications Slack
    # ──────────────────────────────────────────────────────────────
    if should_run("notify"):
        result = run_step("notify", notify_pending_activities, run_id=run_id)
        step_results.append(result)

    # ──────────────────────────────────────────────────────────────
    # Rapport final
    # ──────────────────────────────────────────────────────────────
    overall_status = "success"
    for r in step_results:
        if r["status"] == "failed":
            overall_status = "failed"
            break
        if r["status"] == "warning" and overall_status != "failed":
            overall_status = "warning"

    return _build_report(run_id, pipeline_start, step_results, overall_status)


def _build_report(run_id: str, start: datetime, step_results: list, overall_status: str) -> dict:
    """Construit et affiche le rapport final du pipeline."""
    total_elapsed = (datetime.utcnow() - start).total_seconds()

    logger.info("")
    logger.info("╔" + "═" * 57 + "╗")
    logger.info("║   RAPPORT FINAL DU PIPELINE                             ║")
    logger.info("╠" + "═" * 57 + "╣")
    logger.info("║   Run ID : %-44s ║" % run_id)
    logger.info("║   Durée totale : %-39s ║" % f"{total_elapsed:.1f}s")
    logger.info("║   Statut global : %-38s ║" % overall_status.upper())
    logger.info("╠" + "═" * 57 + "╣")
    for r in step_results:
        icon = "✅" if r["status"] == "success" else ("⚠️ " if r["status"] in ("warning", "skipped") else "❌")
        line = f"{icon} {r['step']:<20} {r['status']:<10} ({r.get('elapsed', 0):.1f}s)"
        logger.info("║   %-54s ║" % line)
    logger.info("╚" + "═" * 57 + "╝")
    logger.info("")

    return {
        "run_id": run_id,
        "status": overall_status,
        "elapsed_seconds": total_elapsed,
        "steps": step_results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sport Data Solution — Pipeline de données",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python pipeline/run_pipeline.py                    # Pipeline complet
  python pipeline/run_pipeline.py --force-regenerate # Regénérer toutes les activités
  python pipeline/run_pipeline.py --skip validate    # Sauter la validation trajets
  python pipeline/run_pipeline.py --only quality     # Uniquement les tests qualité
  python pipeline/run_pipeline.py --only notify      # Uniquement les notifications Slack
        """
    )
    parser.add_argument(
        "--force-regenerate", action="store_true",
        help="Supprimer et regénérer toutes les activités simulées"
    )
    parser.add_argument(
        "--skip", nargs="+", metavar="STEP",
        choices=STEPS,
        help="Étapes à ignorer : " + ", ".join(STEPS)
    )
    parser.add_argument(
        "--only", metavar="STEP",
        choices=STEPS,
        help="Exécuter uniquement cette étape"
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Identifiant de run personnalisé (optionnel)"
    )

    args = parser.parse_args()

    # Créer le dossier logs si nécessaire
    Path("logs").mkdir(exist_ok=True)

    report = run_full_pipeline(
        skip_steps=args.skip or [],
        only_step=args.only,
        force_regenerate=args.force_regenerate,
        run_id=args.run_id,
    )

    sys.exit(0 if report["status"] in ("success", "warning") else 1)
