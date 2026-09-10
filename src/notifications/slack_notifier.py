"""
Notifications Slack — Sport Data Solution.

Envoie un message Slack pour chaque nouvelle activité sportive.

Mode de fonctionnement :
  - Si SLACK_BOT_TOKEN est configuré dans .env → envoi réel sur Slack
  - Sinon → mode DRY-RUN (messages affichés dans les logs, rien n'est envoyé)

Format des messages (exemples) :
  "Bravo Juliette Mendes ! Tu viens de courir 10,8 km en 46 min ! Quelle énergie ! 🔥🏅"
  "Magnifique Laurence Morvan ! Une randonnée de 10 km terminée ! 🌄 (Randonnée de St Guilhem le désert, je vous la conseille c'est top)"

Usage :
  # Envoyer les notifications pour les activités non encore notifiées
  python -m src.notifications.slack_notifier

  # Simuler l'injection d'une activité live et envoyer une notification
  python -m src.notifications.slack_notifier --live --employee-id 12345
"""

import sys
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.orm import Session
from src.config import (
    SLACK_BOT_TOKEN, SLACK_CHANNEL,
    SLACK_TEMPLATES, DEFAULT_SLACK_TEMPLATE
)
from src.database.init_db import get_engine
from src.database.models import Employee, SportActivity, PipelineRun

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger("slack_notifier")

IS_DRY_RUN = not bool(SLACK_BOT_TOKEN)


def format_slack_message(
    employee: Employee,
    activity: SportActivity,
) -> str:
    """
    Formate le message Slack pour une activité sportive donnée.
    Sélectionne un template aléatoire adapté au type de sport.
    """
    sport = activity.sport_type
    templates = SLACK_TEMPLATES.get(sport)

    # Variables disponibles pour les templates
    distance_km = round(activity.distance_meters / 1000, 1) if activity.distance_meters else None
    duree_min = activity.duration_seconds // 60 if activity.duration_seconds else None

    # Format spécial pour la natation (distance en mètres)
    distance_km_natation = activity.distance_meters if activity.distance_meters else None

    # Commentaire formaté (entre parenthèses si présent)
    commentaire_fmt = f" ({activity.comment})" if activity.comment else ""

    if templates:
        template = random.choice(templates)
        try:
            message = template.format(
                prenom=employee.first_name,
                nom=employee.last_name,
                distance_km=distance_km or "?",
                duree_min=duree_min or "?",
                distance_km_natation=distance_km_natation or "?",
                commentaire_fmt=commentaire_fmt,
                sport=sport,
            )
        except KeyError as e:
            logger.warning("  ⚠️  Variable manquante dans template Slack : %s", e)
            message = DEFAULT_SLACK_TEMPLATE.format(
                prenom=employee.first_name,
                nom=employee.last_name,
                sport=sport,
                duree_min=duree_min or "?",
            )
    else:
        message = DEFAULT_SLACK_TEMPLATE.format(
            prenom=employee.first_name,
            nom=employee.last_name,
            sport=sport,
            duree_min=duree_min or "?",
        )

    return message


def send_slack_message(message: str, channel: str = None) -> bool:
    """
    Envoie un message Slack.
    Retourne True si envoi réussi (ou mode dry-run), False si erreur.
    """
    channel = channel or SLACK_CHANNEL

    if IS_DRY_RUN:
        logger.info("  [DRY-RUN] Canal %s : %s", channel, message)
        return True

    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        client = WebClient(token=SLACK_BOT_TOKEN)
        response = client.chat_postMessage(
            channel=channel,
            text=message,
        )
        if response["ok"]:
            logger.info("  ✅ Message Slack envoyé : %s...", message[:60])
            return True
        else:
            logger.error("  ❌ Erreur Slack : %s", response.get("error", "unknown"))
            return False

    except Exception as e:
        logger.error("  ❌ Exception Slack SDK : %s", e)
        return False


def notify_pending_activities(run_id: str = None, batch_size: int = 50) -> dict:
    """
    Envoie les notifications Slack pour toutes les activités non encore notifiées.
    Marque les activités comme notifiées après envoi.
    """
    if run_id is None:
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    started_at = datetime.utcnow()
    step_name = "slack_notifications"

    logger.info("=" * 60)
    logger.info("ÉTAPE : Notifications Slack")
    logger.info("Mode : %s", "DRY-RUN (pas de vrai Slack)" if IS_DRY_RUN else "PRODUCTION (Slack réel)")
    if IS_DRY_RUN:
        logger.info("  → Configurer SLACK_BOT_TOKEN dans .env pour le mode réel")
    logger.info("Canal : %s", SLACK_CHANNEL)
    logger.info("=" * 60)

    engine = get_engine()
    sent = 0
    failed = 0

    try:
        with Session(engine) as session:
            # Récupérer les activités non encore notifiées
            pending = (
                session.query(SportActivity)
                .filter(SportActivity.slack_sent == False)
                .order_by(SportActivity.activity_start.asc())
                .limit(batch_size)
                .all()
            )

            logger.info("%d activité(s) en attente de notification", len(pending))

            for activity in pending:
                try:
                    # Récupérer l'employé associé
                    employee = session.query(Employee).filter_by(
                        employee_id=activity.employee_id
                    ).first()

                    if not employee:
                        logger.warning("  ⚠️  Employé %s introuvable", activity.employee_id)
                        failed += 1
                        continue

                    # Formater et envoyer le message
                    message = format_slack_message(employee, activity)
                    success = send_slack_message(message)

                    if success:
                        activity.slack_sent = True
                        sent += 1
                    else:
                        failed += 1

                except Exception as e:
                    failed += 1
                    logger.error("  ❌ Erreur notification activité %s : %s", activity.id, e)

            session.commit()

        logger.info("✅ %d notifications envoyées, %d échecs", sent, failed)

        # Monitoring
        with Session(engine) as session:
            run = PipelineRun(
                run_id=run_id,
                step_name=step_name,
                status="success" if failed == 0 else "warning",
                records_processed=sent + failed,
                records_inserted=sent,
                errors_count=failed,
                duration_seconds=round((datetime.utcnow() - started_at).total_seconds(), 3),
                details={
                    "mode": "dry_run" if IS_DRY_RUN else "production",
                    "channel": SLACK_CHANNEL,
                    "sent": sent,
                    "failed": failed,
                },
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
            session.add(run)
            session.commit()

        return {"status": "success", "sent": sent, "failed": failed}

    except Exception as e:
        logger.error("❌ Notifications échouées : %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}


def inject_live_activity(employee_id: int, sport_type: str = None) -> dict:
    """
    Injecte une activité live (simulation Strava temps réel) et envoie la notification Slack.
    Utilisé pour la démonstration live du POC.
    """
    logger.info("=" * 60)
    logger.info("INJECTION LIVE — Simulation activité Strava")
    logger.info("Salarié ID : %d", employee_id)
    logger.info("=" * 60)

    engine = get_engine()

    with Session(engine) as session:
        employee = session.query(Employee).filter_by(employee_id=employee_id).first()

        if not employee:
            logger.error("❌ Salarié %d introuvable", employee_id)
            return {"status": "failed", "error": "Employee not found"}

        # Utiliser le sport déclaré si pas spécifié
        if not sport_type:
            sport_type = employee.declared_sport or "Course à pied"

        # Générer une activité réaliste maintenant
        from src.generation.generate_activities import generate_activity_stats, get_comment
        distance_m, duration_s = generate_activity_stats(sport_type)
        comment = get_comment(sport_type)

        now = datetime.utcnow()
        activity = SportActivity(
            employee_id=employee.employee_id,
            activity_start=now - timedelta(seconds=duration_s),
            activity_end=now,
            sport_type=sport_type,
            distance_meters=distance_m,
            duration_seconds=duration_s,
            comment=comment,
            is_live=True,          # Marqué comme activité live
            slack_sent=False,
        )
        session.add(activity)
        session.flush()  # Pour avoir l'ID

        # Envoyer immédiatement la notification Slack
        message = format_slack_message(employee, activity)
        logger.info("Message Slack : %s", message)

        success = send_slack_message(message)
        activity.slack_sent = success
        session.commit()

        dist_str = f"{distance_m/1000:.1f} km" if distance_m else "sans distance"
        logger.info(
            "✅ Activité live insérée — %s %s %s (%s, %d min)",
            employee.first_name, employee.last_name,
            sport_type, dist_str, duration_s // 60
        )

        return {
            "status": "success",
            "employee": employee.full_name,
            "sport": sport_type,
            "distance_km": round(distance_m / 1000, 1) if distance_m else None,
            "duration_min": duration_s // 60,
            "message": message,
            "slack_sent": success,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Notifications Slack — Sport Data Solution")
    parser.add_argument("--live", action="store_true",
                        help="Mode démo live : injecter une activité et notifier")
    parser.add_argument("--employee-id", type=int,
                        help="ID du salarié (requis avec --live)")
    parser.add_argument("--sport", type=str, default=None,
                        help="Type de sport pour l'injection live")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Nombre max de notifications à envoyer")
    args = parser.parse_args()

    if args.live:
        if not args.employee_id:
            logger.error("--employee-id requis avec --live")
            sys.exit(1)
        result = inject_live_activity(args.employee_id, args.sport)
    else:
        result = notify_pending_activities(batch_size=args.batch_size)

    if result["status"] == "failed":
        sys.exit(1)
