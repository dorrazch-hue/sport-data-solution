"""
Modèles SQLAlchemy — Sport Data Solution.
Définit toutes les tables de la base de données PostgreSQL.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Numeric, Boolean,
    DateTime, Date, Text, ForeignKey, UniqueConstraint, Index, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Employee(Base):
    """
    Table principale des salariés (source : fichier RH).
    Données sensibles — accès restreint en production.
    """
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(BigInteger, unique=True, nullable=False, index=True,
                         comment="ID unique du salarié (source RH)")
    last_name = Column(String(100), nullable=False, comment="Nom de famille")
    first_name = Column(String(100), nullable=False, comment="Prénom")
    birth_date = Column(Date, nullable=True, comment="Date de naissance")
    business_unit = Column(String(50), nullable=True, comment="BU : Finance, Marketing, etc.")
    hire_date = Column(Date, nullable=True, comment="Date d'embauche")
    gross_salary = Column(Numeric(10, 2), nullable=False, comment="Salaire brut annuel (€)")
    contract_type = Column(String(10), nullable=True, comment="CDI ou CDD")
    vacation_days = Column(Integer, nullable=True, comment="Nombre de jours de CP")
    home_address = Column(Text, nullable=True, comment="Adresse du domicile déclarée")
    commute_mode = Column(String(50), nullable=True,
                          comment="Mode de déplacement domicile-bureau déclaré")
    declared_sport = Column(String(50), nullable=True,
                            comment="Sport pratiqué déclaré (source données sportives)")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relations
    commute_validations = relationship("CommuteValidation", back_populates="employee",
                                       cascade="all, delete-orphan")
    sport_activities = relationship("SportActivity", back_populates="employee",
                                    cascade="all, delete-orphan")
    benefits = relationship("BenefitCalculation", back_populates="employee",
                            cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Employee {self.employee_id} — {self.first_name} {self.last_name}>"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_sport_commuter(self):
        """Vrai si le mode de déplacement est sportif (marche/running ou vélo/trottinette)."""
        if not self.commute_mode:
            return False
        return self.commute_mode in ["Marche/running", "Vélo/Trottinette/Autres"]


class CommuteValidation(Base):
    """
    Résultats de la validation des trajets domicile-bureau.
    Vérifie la cohérence entre le mode déclaré et la distance réelle.
    """
    __tablename__ = "commute_validations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(BigInteger, ForeignKey("employees.employee_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    home_address = Column(Text, nullable=True, comment="Adresse domicile utilisée pour le calcul")
    company_address = Column(Text, nullable=False, comment="Adresse de l'entreprise")
    distance_km = Column(Numeric(7, 3), nullable=True,
                         comment="Distance calculée domicile-bureau (km)")
    commute_mode = Column(String(50), nullable=True, comment="Mode déclaré par le salarié")
    max_allowed_km = Column(Numeric(5, 1), nullable=True,
                            comment="Distance maximale autorisée pour ce mode (km)")
    is_valid = Column(Boolean, nullable=True,
                      comment="True si la distance est cohérente avec le mode déclaré")
    anomaly_reason = Column(Text, nullable=True,
                            comment="Raison du signalement si is_valid = False")
    validation_method = Column(String(20), nullable=False, default="haversine",
                               comment="'google_maps' ou 'haversine'")
    validated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relation
    employee = relationship("Employee", back_populates="commute_validations")

    __table_args__ = (
        Index("ix_commute_employee_id", "employee_id"),
    )

    def __repr__(self):
        status = "✅ Valide" if self.is_valid else "❌ Anomalie"
        return f"<CommuteValidation emp={self.employee_id} {self.distance_km}km — {status}>"


class SportActivity(Base):
    """
    Activités sportives des salariés.
    Données générées pour le POC (simulation Strava 12 mois).
    Pourra être alimentée par l'API Strava réelle en production.
    """
    __tablename__ = "sport_activities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(BigInteger, ForeignKey("employees.employee_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    activity_start = Column(DateTime, nullable=False, index=True,
                            comment="Date et heure de début de l'activité")
    activity_end = Column(DateTime, nullable=True,
                          comment="Date et heure de fin de l'activité")
    sport_type = Column(String(50), nullable=False, comment="Type de sport")
    distance_meters = Column(Integer, nullable=True,
                             comment="Distance en mètres (NULL pour sports sans distance)")
    duration_seconds = Column(Integer, nullable=True,
                              comment="Durée totale en secondes")
    comment = Column(Text, nullable=True, comment="Commentaire libre du salarié")
    is_live = Column(Boolean, default=False, nullable=False,
                     comment="True = injection live (démo Strava), False = données simulées")
    slack_sent = Column(Boolean, default=False, nullable=False,
                        comment="True si la notification Slack a été envoyée")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relation
    employee = relationship("Employee", back_populates="sport_activities")

    __table_args__ = (
        Index("ix_activity_employee_date", "employee_id", "activity_start"),
        Index("ix_activity_sport_type", "sport_type"),
        Index("ix_activity_is_live", "is_live"),
    )

    def __repr__(self):
        dist = f"{self.distance_meters}m" if self.distance_meters else "sans distance"
        return f"<SportActivity emp={self.employee_id} {self.sport_type} {dist} @{self.activity_start}>"

    @property
    def distance_km(self):
        """Distance en km, ou None."""
        if self.distance_meters is None:
            return None
        return round(self.distance_meters / 1000, 2)

    @property
    def duration_minutes(self):
        """Durée en minutes, ou None."""
        if self.duration_seconds is None:
            return None
        return self.duration_seconds // 60


class BenefitCalculation(Base):
    """
    Résultat des calculs d'avantages salariaux.
    Calculé une fois par année par salarié.
    Peut être recalculé si les paramètres ou les données changent.
    """
    __tablename__ = "benefits_calculations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employee_id = Column(BigInteger, ForeignKey("employees.employee_id", ondelete="CASCADE"),
                         nullable=False, index=True)
    calculation_year = Column(Integer, nullable=False, comment="Année du calcul (ex : 2026)")

    # --- Prime sportive ---
    eligible_sport_bonus = Column(Boolean, default=False, nullable=False,
                                  comment="Éligible à la prime sportive 5%")
    sport_bonus_amount = Column(Numeric(10, 2), default=0.00, nullable=False,
                                comment="Montant de la prime (€)")
    sport_bonus_reason = Column(Text, nullable=True,
                                comment="Raison d'éligibilité ou d'inéligibilité")
    commute_distance_km = Column(Numeric(7, 3), nullable=True,
                                 comment="Distance domicile-bureau calculée (km)")
    commute_is_valid = Column(Boolean, nullable=True,
                              comment="True si le trajet déclaré est cohérent")

    # --- Jours bien-être ---
    activity_count_year = Column(Integer, default=0, nullable=False,
                                 comment="Nombre d'activités sportives dans l'année")
    eligible_wellness_days = Column(Boolean, default=False, nullable=False,
                                    comment="Éligible aux 5 jours bien-être")
    wellness_days_count = Column(Integer, default=0, nullable=False,
                                 comment="Nombre de jours bien-être accordés")
    wellness_reason = Column(Text, nullable=True,
                             comment="Raison d'éligibilité ou d'inéligibilité")

    # --- Impact financier ---
    total_cost_company = Column(Numeric(10, 2), default=0.00, nullable=False,
                                comment="Coût total estimé pour l'entreprise (€)")

    calculated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relation
    employee = relationship("Employee", back_populates="benefits")

    __table_args__ = (
        UniqueConstraint("employee_id", "calculation_year",
                         name="uq_benefits_employee_year"),
        Index("ix_benefits_employee_year", "employee_id", "calculation_year"),
    )

    def __repr__(self):
        prime = f"prime={self.sport_bonus_amount}€" if self.eligible_sport_bonus else "pas de prime"
        jours = f"bien-être={self.wellness_days_count}j" if self.eligible_wellness_days else "pas de jours bien-être"
        return f"<BenefitCalculation emp={self.employee_id} {self.calculation_year} — {prime}, {jours}>"


class PipelineRun(Base):
    """
    Table de monitoring du pipeline.
    Chaque étape du pipeline enregistre son statut et ses métriques.
    """
    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(100), nullable=False, index=True,
                    comment="Identifiant unique de l'exécution (ex: 2026-01-15_ingest_rh)")
    step_name = Column(String(50), nullable=False,
                       comment="Nom de l'étape : ingest_rh, validate_commutes, etc.")
    status = Column(String(20), nullable=False, default="running",
                    comment="'running' | 'success' | 'failed' | 'warning'")
    records_processed = Column(Integer, default=0, nullable=False,
                               comment="Nombre d'enregistrements traités")
    records_inserted = Column(Integer, default=0, nullable=False,
                              comment="Nombre d'enregistrements insérés/mis à jour")
    errors_count = Column(Integer, default=0, nullable=False,
                          comment="Nombre d'erreurs rencontrées")
    warnings_count = Column(Integer, default=0, nullable=False,
                            comment="Nombre d'avertissements")
    duration_seconds = Column(Numeric(8, 3), nullable=True,
                              comment="Durée d'exécution en secondes")
    details = Column(JSON, nullable=True,
                     comment="Informations complémentaires (JSON)")
    error_message = Column(Text, nullable=True, comment="Message d'erreur si status=failed")
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_pipeline_run_id", "run_id"),
        Index("ix_pipeline_step_status", "step_name", "status"),
        Index("ix_pipeline_started_at", "started_at"),
    )

    def __repr__(self):
        return (
            f"<PipelineRun {self.run_id} | {self.step_name} | "
            f"{self.status} | {self.records_processed} records>"
        )
