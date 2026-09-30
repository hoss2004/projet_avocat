# LEGAL AI TUNISIA — phases 1 à 4

Socle documentaire juridique pour un avocat en Tunisie : PDF originaux conservés, extraction traçable, articles identifiables, versions distinctes et API protégée. **Aucun LLM, aucune traduction automatique, aucun envoi cloud, aucun scraper.**

## État livré

| Phase | Livrable |
|---|---|
| 1 | FastAPI, configuration, Docker Compose, PostgreSQL 17 avec pgvector |
| 2 | SQLAlchemy 2, migration Alembic, documents/versions/articles, repository filtré par cabinet |
| 3 | Upload PDF, empreinte SHA-256, déduplication par cabinet, stockage original, extraction par page, détection de langue |
| 4 | Détection d'articles AR/FR, hiérarchie juridique, positions source, consultation et recherche exacte via API |

Le démarrage Docker et la RLS sur serveur réel doivent être vérifiés sur une machine disposant de Docker. Ils ne sont pas présentés comme validés par les tests SQLite. Voir `docs/VALIDATION.md`.

## Démarrage avec Docker

Prérequis : Docker Engine/Desktop avec Compose v2 et conteneurs Linux ; Python seulement pour le script facultatif de configuration. Le conteneur utilise Python 3.12. Le code reste compatible Python 3.11 pour les tests locaux.

Depuis la racine du projet :

```powershell
python scripts/setup_env.py
docker compose up --build -d
docker compose ps -a
```

Le script génère `.env`, des mots de passe différents, une clé API et un identifiant de cabinet. Il refuse d'écraser un `.env` existant et n'affiche aucun secret. Si `.env` est déjà présent, passer directement à Docker. Alternative : copier `.env.example` vers `.env` et remplacer les valeurs.

- API : http://127.0.0.1:8000
- Swagger : http://127.0.0.1:8000/docs
- Santé du processus : `GET /health` (sans authentification ; ne teste pas PostgreSQL).
- Dans Swagger, cliquer **Authorize** et fournir `API_KEY` de `.env`.

`postgres` initialise le rôle applicatif ; `migrate` applique Alembic et se termine ; `backend` démarre seulement après réussite de la migration. L'état « Exited (0) » de `migrate` est attendu. PostgreSQL n'est pas exposé sur un port de l'hôte. L'API écoute seulement sur l'interface locale.

```powershell
docker compose logs migrate backend
docker compose stop
```

Les volumes persistent après l'arrêt. Ne pas supprimer les volumes pour une simple mise à jour. Le script d'initialisation PostgreSQL ne s'exécute que sur un volume neuf : changer un mot de passe dans `.env` ne change pas celui d'un rôle déjà créé.

## Configuration

| Variable | Usage |
|---|---|
| `POSTGRES_PASSWORD` | Administration et migrations |
| `APP_DB_PASSWORD` | Initialisation du rôle limité `legal_app` |
| `DATABASE_URL` | Connexion du backend avec `legal_app` |
| `MIGRATION_DATABASE_URL` | Connexion administrative du service `migrate` |
| `API_KEY` | Secret d'au moins 32 caractères, en-tête `X-API-Key` |
| `TENANT_ID` | UUID du cabinet de cette instance |
| `STORAGE_PATH` | Répertoire du stockage original ; `/data/storage` dans Docker |
| `MAX_UPLOAD_SIZE` | Limite de taille du PDF en octets, 25 Mio par défaut |
| `MAX_PDF_PAGES` | 1 500 pages par défaut |

Les mots de passe générés sont hexadécimaux, donc compatibles avec les URL. Encoder les caractères réservés si vous choisissez d'autres mots de passe. Ne jamais versionner `.env`. Les paramètres embeddings, reranker et LLM seront ajoutés avec leurs implémentations, aux phases 5 à 10.

## Architecture et arborescence

```text
backend/
  app/
    main.py
    cli.py
    core/                # configuration, authentification, sessions, limite upload, logs
    api/routes/          # santé, documents et articles
    models/              # modèles courants et instantané de migration v1
    schemas/             # contrats de validation Pydantic
    repositories/        # accès SQL filtrés par tenant
    services/
      ingestion/         # upload, déduplication, transaction d'ingestion
      language/          # détection de script/langue
    parsers/             # extraction PDF et structure juridique
    utils/               # normalisation de recherche arabe
  alembic/
  tests/
    fixtures/            # exclusivement TEST LAW / TEST ARTICLE
  requirements.lock
  requirements-test.lock
postgres/init.sql
scripts/setup_env.py
scripts/import_pdf.py
data/legal/              # classement logique du corpus
  constitution/ codes/ tax/ special_laws/ decrees/ decree_laws/ orders/ jort/ jurisprudence/
data/cabinet/             # emplacements réservés ; aucune API cabinet dans cette phase
data/cases/               # réservé ; aucun dossier client ingéré
```

Le classement `data/legal/` n'est pas un dossier surveillé : l'import reste manuel. Les originaux déposés par API sont stockés dans un volume privé sous `<tenant UUID>/<document UUID>.pdf`. Les 18 PDF déjà présents à la racine ne sont ni déplacés ni modifiés.

Flux : validation de taille et signature → SHA-256 → détection de doublon → conservation du fichier → ingestion explicite → extraction par page → langue → titres/articles → transaction SQL. Les services d'ingestion sont séparés des routes pour permettre une file de travaux ultérieure.

## Installer les outils locaux et lancer les tests

PowerShell, depuis la racine :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock -r backend/requirements-test.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e backend
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Linux/macOS : remplacer `.\.venv\Scripts\python.exe` par `.venv/bin/python`.

Les fichiers de dépendances figent les versions vérifiées localement. `pyproject.toml` indique les plages compatibles ; les fichiers lock sont utilisés par Docker. Leur résolution sur Linux/Python 3.12 reste à confirmer par le build Docker.

Les tests couvrent l'arabe, les articles multiligne/RTL, les positions source, les PDF invalides ou sans texte, les tailles limites, l'authentification, les frontières de cabinets, la déduplication, la réingestion et les versions. Ils utilisent SQLite pour les parcours API. Le test PostgreSQL est séparé et ignoré si ses URL ne sont pas configurées.

Sur Docker, le service de test active la vérification PostgreSQL/pgvector, avec contrôle de la RLS par SQL direct :

```powershell
docker compose --profile test run --build --rm tests
```

Ce test insère une ligne `TEST LAW` avec des UUID aléatoires puis supprime uniquement cette ligne via le rôle d'administration. Les tests unitaires ne chargent aucun droit tunisien réel.

## Importer un code tunisien

Aucune source n'est déclarée officielle automatiquement. Donner un titre, puis renseigner la provenance et les dates après vérification documentaire. Ne pas déduire la date d'entrée en vigueur du nom du fichier.

Depuis Swagger :

1. `POST /legal-documents/upload` : fichier PDF + champ `metadata` contenant un objet JSON.
2. Copier l'identifiant du document.
3. `POST /legal-documents/ingest` avec `{"document_id":"UUID"}`.
4. Consulter `GET /legal-documents/{id}/articles`.

Métadonnées minimales :

```json
{
  "title_ar": "مجلة المرافعات المدنية والتجارية",
  "title_fr": "Code de procédure civile et commerciale",
  "document_type": "code",
  "official": false,
  "status": "unknown"
}
```

Pour une source vérifiée, compléter `source_url`, `source_name`, `publication_date`, `effective_from` et, si justifié, `official: true`. Ce booléen reste une déclaration de l'importateur, pas une certification du logiciel.

Exemple avec le script client (utilise la clé dans `.env`) :

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe scripts/import_pdf.py Code_de_procedure_civile_et_commerciale.pdf --title-fr "Code de procédure civile et commerciale" --article 403
```

Le script affiche le statut, les avertissements et les articles. Sans `--article`, la première page de résultats est affichée (50 articles). L'API utilise `offset` et `limit` (maximum 200) pour la pagination. Pour une nouvelle version, ajouter `--previous-version-id UUID`.

Inspection locale sans base ni serveur :

```powershell
.\.venv\Scripts\python.exe -m app.cli Code_de_procedure_civile_et_commerciale.pdf --article 403
```

Cette commande ne modifie pas le PDF. Elle ne certifie ni l'authenticité de la source ni sa validité actuelle.

## API livrée

Toutes les routes documentaires demandent `X-API-Key`. Les ressources inconnues et celles d'un autre cabinet retournent toutes deux 404.

| Méthode | Route | Fonction |
|---|---|---|
| GET | `/health` | Santé du processus |
| POST | `/legal-documents/upload` | PDF multipart + métadonnées JSON |
| POST | `/legal-documents/ingest` | Extraire et structurer un document déjà téléversé |
| GET | `/legal-documents` | Liste paginée |
| GET | `/legal-documents/{id}` | Métadonnées, version, liens précédent/suivant |
| GET | `/legal-documents/{id}/articles` | Articles paginés ; filtre exact `article_number` |
| GET | `/legal-documents/{id}/pages/{page}` | Texte extrait d'une page PDF (numérotation à partir de 1) |
| GET | `/legal-documents/{id}/file` | Télécharger le PDF original |
| GET | `/legal-articles/{id}` | Article avec texte original, pages, offsets et empreinte |

Le filtre accepte `403`, `article 403` ou `الفصل ٤٠٣`. Il est toujours associé à un document, pour ne pas confondre deux codes. Plusieurs articles du même numéro peuvent exister dans un PDF composite : tous sont renvoyés, avec un avertissement ; aucun n'est arbitrairement écrasé.

Le téléchargement nécessite l'en-tête d'authentification : un futur frontend devra le fournir, puis ouvrir le PDF à la page concernée.

## Conservation des textes et qualité

Le **PDF d'origine** est la source documentaire conservée. `original_text` est sa couche texte extraite, qui peut différer de la lecture visuelle en cas de police ou d'ordre RTL incorrect. Elle n'est jamais normalisée en place.

- `text_ar` contient l'extrait original lorsqu'il est détecté arabe.
- `text_fr` contient uniquement un extrait d'origine français, jamais une traduction générée.
- `normalized_text_for_search` est une copie dérivée : NFKC, variantes d'alef, chiffres, diacritiques et tatweel.
- Les articles conservent `book`, `title_section`, `chapter`, `section`, pages et offsets `[start_offset, end_offset)` dans le texte complet.
- Les pages sont reliées par `\n\f\n`, séparateur déterministe.
- Les empreintes SHA-256 couvrent le PDF et chaque extrait UTF-8.

Le parseur reconnaît les en-têtes usuels, les chiffres arabo-indiens/persans, les formes de présentation Unicode, les suffixes `مكرر`, `bis`, `ter`, `quater`, et certains en-têtes numériques en ordre visuel RTL. Il ne coupe pas arbitrairement tous les 1 000 caractères.

Statuts :

| Statut | Sens |
|---|---|
| `uploaded` | Original conservé, ingestion à lancer |
| `needs_review` | Articles extraits, contrôle humain nécessaire |
| `no_articles` | Texte extrait mais aucun en-tête fiable reconnu |
| `ocr_required` | Scan, couche texte absente ou encodage inexploitable |
| `failed` | PDF chiffré, invalide ou hors limites d'extraction |

`needs_review` n'est pas une validation juridique. Le contrôle automatique ne distingue pas toujours les sommaires, notes, annexes, lois introductives et corps du code. Certaines premières lignes ou numérotations dégradées peuvent être manquées. Le texte original des pages et le PDF restent consultables. Une correction éditoriale/validation du découpage sera une extension explicite.

Une interface `OCRProvider` est prévue, sans OCR exécuté. Aucun contenu manquant n'est reconstruit ou inventé. La détection FR/EN est une heuristique limitée ; `und` représente une langue indéterminée.

## Modèles et versions

- `LegalDocument` : original, cabinet, métadonnées de provenance, langue, extraction et statut d'ingestion.
- `LegalVersion` : série documentaire, numéro, dates déclarées de publication/effet/modification/abrogation, statut juridique et précédent.
- `LegalArticle` : article associé au document et à sa version, hiérarchie, texte et traçabilité.

Un import nouveau peut désigner `previous_version_id` du même cabinet. Le numéro de version augmente ; le lien `next_version_id` est calculé depuis le successeur. Un précédent ne peut avoir qu'un successeur. Les lignes précédentes ne sont pas écrasées et leurs dates ne sont pas inventées. L'historique est stocké ; la sélection temporelle du droit applicable et la résolution des périodes qui se chevauchent ne sont pas implémentées dans ces phases.

Un même PDF binaire réimporté dans le même cabinet retourne le document existant. Les nouvelles métadonnées ne sont pas appliquées silencieusement. Un changement de métadonnées à fichier identique nécessitera la future API d'administration.

## Migrations

```powershell
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic upgrade head
```

La migration initiale active `vector`, crée les tables/index/contraintes et applique les politiques RLS. Son schéma est figé dans `models/migration_v1.py` ; ne pas modifier cet instantané lors d'une évolution : ajouter une migration. Aucun vecteur n'est calculé dans ces phases et il n'y a pas encore de colonne d'embedding, car le modèle et sa dimension seront choisis en phase 5.

## Sécurité et exploitation

Cette version utilise **un cabinet configuré et une clé API par instance**, sans gestion des comptes individuels. Le cabinet est dérivé de la configuration authentifiée, jamais d'un en-tête `tenant_id` fourni par le client. Les repositories filtrent chaque lecture avant exécution ; les écritures utilisent le même cabinet.

PostgreSQL applique en plus une RLS forcée sur les trois tables, avec `app.tenant_id` fixé localement à chaque transaction. Le rôle `legal_app` n'est ni propriétaire, ni superutilisateur, ni autorisé à contourner la RLS. Le backend ne reçoit pas les identifiants de migration. Les migrations utilisent un service distinct. Ne pas remplacer `DATABASE_URL` par une connexion d'administration.

Le stockage utilise des UUID internes, jamais le nom du fichier comme chemin. La taille du corps HTTP est limitée avant le parsing multipart, puis la taille du PDF est contrôlée. L'extraction limite les pages et le volume de texte. L'ingestion est synchrone ; un worker isolé avec quotas CPU/mémoire est à prévoir pour un déploiement exposé à des fichiers non fiables.

Les logs applicatifs sont JSON et n'incluent ni clé API, ni texte documentaire. Le journal d'audit nominatif, les permissions utilisateur/dossier, les sauvegardes chiffrées, la gestion de rétention, TLS et le déploiement multi-utilisateur restent à implémenter avant un usage partagé en production. Sauvegarder ensemble la base et le volume des originaux. Un arrêt brutal entre l'écriture d'un PDF et le commit SQL peut laisser un fichier orphelin ; aucune purge automatique n'est exécutée.

## Suite du projet

Phases 5–8 : fournisseurs d'embeddings multilingues interchangeables, vecteurs pgvector, FTS/exact lookup, fusion hybride, expansion FR/AR et reranking. Le choix du modèle devra être évalué sur des questions françaises et des sources arabes vérifiées ; aucun fournisseur n'est imposé aujourd'hui.

Phases 9–10 : `LLMProvider`, prompt juridique, citations structurées et validation des références. **Pas d'endpoint RAG ou de réponse juridique simulée dans ce livrable.**

Phases 11–12 : dossiers et pièces, chronologie, permissions, recherche dossier + droit. Phase 13 : frontend React/Next.js avec sources et RTL.

Les critères de recherche sémantique française sur des textes arabes et de réponse RAG concernent ces phases suivantes. La livraison actuelle permet l'ingestion, la structuration et la consultation exacte.

## Références techniques

- [FastAPI : fichiers multipart](https://fastapi.tiangolo.com/tutorial/request-files/)
- [PyMuPDF : extraction de texte et ordre de lecture](https://pymupdf.readthedocs.io/en/latest/app1.html)
- [PostgreSQL 17 : Row Level Security](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)

Ces références documentent les composants ; elles ne constituent pas des sources de droit tunisien.
