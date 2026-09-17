# Sport Data Solution — POC Avantages Sportifs

Pipeline de données de bout en bout pour le suivi et le calcul des avantages sportifs des salariés.

## Contexte

Sport Data Solution souhaite récompenser les salariés pratiquant une activité physique régulière via deux avantages :

1. **Prime sportive (5% du salaire brut)** — pour les salariés venant au bureau à pied, en vélo ou trottinette
2. **5 jours bien-être** — pour les salariés ayant au moins 15 activités physiques dans l'année

---

## Architecture du projet

```
sport-data-solution/
├── data/raw/                   ← Données sources (RH + sportives)
├── src/
│   ├── config.py               ← Configuration centralisée (.env)
│   ├── database/
│   │   ├── models.py           ← Modèles SQLAlchemy (5 tables)
│   │   └── init_db.py          ← Initialisation PostgreSQL
│   ├── ingestion/
│   │   └── ingest_rh.py        ← Lecture Excel → PostgreSQL
│   ├── validation/
│   │   └── validate_commute.py ← Vérification trajets (Google Maps / Haversine)
│   ├── generation/
│   │   └── generate_activities.py ← Simulation Strava 12 mois (~3000 lignes)
│   ├── transformation/
│   │   └── compute_benefits.py ← Calcul primes et jours bien-être
│   ├── quality/
│   │   └── data_quality.py     ← Tests d'intégrité (18 tests)
│   └── notifications/
│       └── slack_notifier.py   ← Messages Slack par activité
├── pipeline/
│   └── run_pipeline.py         ← Exécution manuelle du pipeline complet
├── airflow/dags/
│   └── sport_data_pipeline.py  ← DAG Airflow (orchestration automatique)
├── docker-compose.yml          ← PostgreSQL + pgAdmin + Airflow
├── requirements.txt
└── .env.example
```

### Schéma de la base de données

| Table | Description |
|-------|-------------|
| `employees` | Salariés (source RH + sportif) |
| `commute_validations` | Résultats validation trajets domicile-bureau |
| `sport_activities` | Activités sportives (simulées ou live Strava) |
| `benefits_calculations` | Avantages calculés par salarié et par année |
| `pipeline_runs` | Monitoring de chaque étape du pipeline |

### Pipeline des données

```
[Excel RH + Sportif]
        ↓
  1. Ingestion RH → employees (161 salariés)
        ↓
  2. Validation trajets  →  commute_validations
     (Google Maps ou Haversine)
        ↓
  3. Génération activités → sport_activities (~3000 lignes, 12 mois)
     (simulation Strava)
        ↓
  4. Calcul avantages → benefits_calculations
     (prime 5% + jours bien-être)
        ↓
  5. Tests qualité (18 tests sur 4 tables)
        ↓
  6. Notifications Slack (par activité sportive)
        ↓
  [Metabase] — Visualisation KPIs
```

---

## Installation

### Prérequis

- Docker Desktop installé et démarré
- Python 3.11+
- Git

### 1. Cloner le projet

```bash
git clone https://github.com/dorrazch-hue/sport-data-solution.git
cd sport-data-solution
```

### 2. Configurer l'environnement

```bash
cp .env.example .env
```

Éditer `.env` avec vos valeurs (les valeurs par défaut suffisent pour démarrer) :

```bash
# Obligatoire — changer le mot de passe
DB_PASSWORD=mon_mot_de_passe_securise

# Optionnel — pour la validation des trajets précise
GOOGLE_MAPS_API_KEY=...

# Optionnel — pour les vraies notifications Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#sport-activites
```

### 3. Démarrer l'infrastructure Docker

```bash
docker-compose up -d
```

Services disponibles :
- **PostgreSQL** : `localhost:5432`
- **pgAdmin** : [http://localhost:5050](http://localhost:5050) (admin@sportdata.fr / voir .env)
- **Airflow** : [http://localhost:8080](http://localhost:8080) (admin / admin)

### 4. Installer les dépendances Python

```bash
pip install -r requirements.txt
```

### 5. Placer les données sources

Copier les fichiers dans `data/raw/` :
- `donnees_rh.xlsx`
- `donnees_sportives.xlsx`

---

## Exécution du pipeline

### Mode manuel (sans Airflow)

```bash
# Pipeline complet
python pipeline/run_pipeline.py

# Regénérer toutes les activités (depuis zéro)
python pipeline/run_pipeline.py --force-regenerate

# Sauter la validation des trajets (si pas de clé Google Maps)
python pipeline/run_pipeline.py --skip validate

# Uniquement les tests de qualité
python pipeline/run_pipeline.py --only quality

# Uniquement les notifications Slack
python pipeline/run_pipeline.py --only notify
```

### Mode Airflow (orchestration automatique)

```bash
# Démarrer Airflow (inclus dans docker-compose)
docker-compose up -d

# Accéder à l'interface : http://localhost:8080
# Login : admin / admin
# Activer le DAG "sport_data_pipeline"
```

Le DAG s'exécute automatiquement chaque jour à 6h00.

### Démonstration live (simulation Strava temps réel)

```bash
# Injecter une activité live et envoyer la notification Slack
python -m src.notifications.slack_notifier --live --employee-id 43015

# Avec un sport spécifique
python -m src.notifications.slack_notifier --live --employee-id 35731 --sport Randonnée
```

---

## Configuration des APIs (optionnel)

### Google Maps API

La validation des trajets utilise la formule haversine si aucune clé n'est configurée (moins précis mais fonctionnel). Pour activer Google Maps :

1. Aller sur [console.cloud.google.com](https://console.cloud.google.com)
2. Créer un projet ou en sélectionner un
3. Activer **Distance Matrix API** (APIs & Services → Enable APIs)
4. Créer une clé API (APIs & Services → Credentials)
5. Ajouter dans `.env` : `GOOGLE_MAPS_API_KEY=votre_cle`

**Note** : La Distance Matrix API est gratuite pour les 40 000 premiers éléments/mois (200$/mois de crédit offert).

### Slack Bot

Pour activer les vraies notifications Slack :

1. Aller sur [api.slack.com/apps](https://api.slack.com/apps)
2. **Create New App** → From scratch → Nom : "Sport Data Solution"
3. **OAuth & Permissions** → Scopes → Bot Token Scopes → ajouter `chat:write`
4. **Install to Workspace** → Autoriser
5. Copier le **Bot User OAuth Token** (`xoxb-...`)
6. Inviter le bot dans votre canal : `/invite @sport-data-solution`
7. Ajouter dans `.env` :
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_CHANNEL=#sport-activites
   ```

**Sans token** : les messages Slack sont affichés dans les logs (mode dry-run).

---

## Paramètres métier configurables

Tous les seuils sont modifiables dans `.env` **sans toucher au code** :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SPORT_BONUS_RATE` | `0.05` | Taux prime sportive (5%) |
| `WELLNESS_DAYS_THRESHOLD` | `15` | Activités minimum pour jours bien-être |
| `WELLNESS_DAYS_COUNT` | `5` | Nombre de jours bien-être accordés |
| `MAX_DISTANCE_WALKING_KM` | `15.0` | Distance max marche/running (km) |
| `MAX_DISTANCE_CYCLING_KM` | `25.0` | Distance max vélo/trottinette (km) |

---

## Tests de qualité

18 tests automatisés couvrent 4 tables :

**employees** (6 tests) : unicité IDs, salaires positifs, modes de déplacement valides, noms non vides, types de contrat, dates d'embauche

**sport_activities** (7 tests) : distances non négatives, durées positives, dates dans la période, fin > début, sport non null, clé étrangère employé, pas de dates futures

**benefits_calculations** (4 tests) : cohérence montant prime (salaire × 5%), jours bien-être (5 si éligible / 0 sinon), cohérence activités/éligibilité, coût non négatif

**commute_validations** (1 test) : tous les modes sportifs ont une validation

---

## Visualisation Metabase

Metabase est inclus dans le docker-compose (port 3000).  
Dashboard **"Sport Data Solution — KPIs"** avec 4 graphiques :

- **Éligibilité prime sportive** — répartition éligibles/non éligibles (Benefits Calculations)
- **Activités sportives par type** — top sports pratiqués (Sport Activities)
- **Total primes versées** — montant par éligibilité (Benefits Calculations)
- **Employés par Business Unit** — répartition des salariés par département (Employees)

Accès : [http://localhost:3000](http://localhost:3000)

---

## Structure des données

### Table `employees` (161 lignes)

| Colonne | Type | Description |
|---------|------|-------------|
| employee_id | int | ID unique salarié |
| last_name / first_name | str | Nom et prénom |
| gross_salary | decimal | Salaire brut annuel (€) |
| commute_mode | str | Mode de déplacement déclaré |
| declared_sport | str | Sport pratiqué déclaré |
| home_address | str | Adresse du domicile |

### Table `sport_activities` (~3000 lignes)

| Colonne | Type | Description |
|---------|------|-------------|
| employee_id | int | Lien salarié |
| activity_start | datetime | Début de l'activité |
| sport_type | str | Type de sport |
| distance_meters | int | Distance (m) ou NULL |
| duration_seconds | int | Durée (s) |
| comment | str | Commentaire libre |
| is_live | bool | True = activité live (démo Strava) |

---

## Sécurité

- Les données RH sont dans `data/raw/` — **ne jamais committer** (inclus dans `.gitignore`)
- Le fichier `.env` n'est **jamais committé** (`.gitignore`)
- Les mots de passe sont externalisés dans `.env`
- En production, utiliser des secrets managers (Vault, AWS Secrets Manager, etc.)

---

## Équipe

Projet POC développé pour Sport Data Solution.
Fondateurs : Alexandre (vélo de route) & Juliette (marathon).
