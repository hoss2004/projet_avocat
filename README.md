# LEGAL AI TUNISIA — assistant documentaire juridique

Application locale pour importer des textes et des pièces, rechercher en français/arabe et obtenir des réponses provisoires accompagnées d'extraits vérifiables. Le PDF original reste la référence. Aucun texte n'est déclaré officiel ou à jour automatiquement.

Les corrections de recherche et l’analyse complète des dossiers sont décrites dans [Validation du retrieval](docs/RETRIEVAL_VALIDATION.md) et [Analyse des dossiers](docs/CASE_ANALYSIS.md).

## Ouvrir l'application

```powershell
docker compose up --build -d
```

Après installation, un double-clic sur `DEMARRER.bat` redémarre les services et ouvre l’application. Docker Desktop et Ollama doivent être démarrés.

Ouvrir **http://localhost:3000**, puis se connecter avec la valeur `API_KEY` du fichier `.env`. Cette clé est déjà générée sur le poste de développement. Ne pas la communiquer ni la copier dans `.env.example`.

- **Bibliothèque** : importer un PDF juridique, consulter son diagnostic, indexer les passages.
- **Dossiers** : créer une affaire, déposer plusieurs pièces, lancer le rapport complet, vérifier les faits et préparer un brouillon.
- **Assistant** : poser une question FR/AR, choisir le dossier, le texte et éventuellement la date des faits ; ouvrir les extraits et le PDF à la page source.
- L'historique de conversation reste en mémoire de l'onglet et se réinitialise lors d'un changement de périmètre. Le journal d'audit conserve les identifiants de sources et une empreinte de la question, pas le texte des échanges.

L'API technique reste accessible sur http://localhost:8000/docs. Une session navigateur dure huit heures ; sa clé n'est jamais renvoyée au navigateur après connexion. Les deux ports sont limités à la boucle locale.

## Activer le modèle local

Avec Ollama installé sur Windows :

```powershell
ollama pull qwen2.5:3b
ollama pull bge-m3
```

Ollama doit être démarré. Les téléchargements initiaux sont volumineux ; ils ne transmettent pas les documents. Configuration `.env` :

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=qwen2.5:3b
OLLAMA_URL=http://host.docker.internal:11434
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3
ALLOW_REMOTE_LLM=false
MODEL_TIMEOUT=180
```

Puis `docker compose up -d backend`. Dans Bibliothèque, cliquer **Indexer les passages**. L'indexation se fait par petits lots et peut être interrompue après le lot courant. Les longs articles ont une représentation vectorielle basée sur les 12 000 premiers caractères ; Ollama peut également limiter cet extrait à sa capacité en tokens, ce qui est signalé dans les métadonnées ; leur texte complet reste disponible pour la recherche textuelle et le PDF original. Le modèle/dimension ne sont jamais mélangés entre deux configurations : les vecteurs sont associés à leur fournisseur et modèle.

Alternative, Ollama dans Docker (CPU par défaut) :

```powershell
docker compose --profile local-ai up -d ollama
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull bge-m3
```

Dans ce cas, utiliser `OLLAMA_URL=http://ollama:11434`, puis recréer le backend. Ne pas lancer simultanément les deux serveurs par nécessité : choisir celui adapté au poste. Le profil Docker est facultatif ; sous Windows, Ollama natif peut utiliser le GPU s'il est pris en charge.

**Sans modèle installé ou en cas de réponse rejetée**, le chat affiche les extraits documentaires et une explication. Il ne simule pas une analyse IA.

## Service IA alternatif

Un fournisseur exposant `/chat/completions` avec réponse JSON peut être configuré par `LLM_PROVIDER=compatible`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`. L'envoi à un hôte externe exige en plus `ALLOW_REMOTE_LLM=true`. Choisir explicitement ce mode seulement si la transmission des passages sélectionnés est autorisée pour le cabinet. Le backend n'envoie pas toute la bibliothèque au modèle de rédaction.

## Recherche et réponses

1. Analyse structurée de la question : treize domaines, sous-domaine, concepts FR/AR, codes préférés et références exactes. Le vocabulaire `LegalTerm` est isolé par cabinet.
2. Filtrage d’accès cabinet/dossier/date. Le domaine détecté ne crée aucun filtre absolu sur un code.
3. Recherche sémantique de la question originale et de la requête arabe enrichie ; FTS original/arabe et recherche combinant les concepts juridiques.
4. Fusion RRF et reranking métier : cohérence des concepts dans un passage, domaine, code, références exactes et similarité.
5. Seuil minimal, seuil relatif pour la question de revendication par un tiers et diversité des sources. Aucun remplissage automatique de top_k.
6. Mode `debug=true` sur les requêtes de recherche/RAG : compréhension, candidats, canaux, composantes du score, sélection/rejet. Les scores ne sont pas des probabilités.
7. Contexte borné, citations contraintes avec Ollama et validation avant affichage. Les originaux restent distincts des projections normalisées.

Les citations vérifiées **ne prouvent pas automatiquement que l'interprétation juridique est correcte**. Les réponses sont provisoires. Le système signale les extractions à vérifier, les dates d'effet inconnues, les sources manquantes et les indisponibilités du modèle. Aucun score de confiance global arbitraire n'est affiché.

Une date des faits exclut les lois dont le début de validité est inconnu. Les intervalles sont `[effective_from, effective_to)`. Les dates et statuts sont renseignés par l'importateur via les métadonnées de l'API ; l'interface d'administration des versions reste à étendre. Sans date, différentes versions peuvent être retournées et doivent être contrôlées.

## Dossiers et confidentialité

Les pièces sont découpées par paragraphes et pages, avec positions conservées. Les références croisées de fichiers restent limitées au dossier sélectionné. La chronologie extrait les mentions de dates numériques ; elle ne prétend pas établir les faits ni calculer les échéances. Les extractions structurées du rapport conservent parties, demandes, montants, observations et provenance ; elles sont à vérifier. Les contradictions détectées restent des hypothèses de rapprochement.

Un cabinet par instance, identifié après authentification. Les repositories filtrent les ressources ; PostgreSQL applique également une RLS forcée sur toutes les tables métier. Le backend utilise `legal_app`, sans privilège superutilisateur. Les migrations emploient un service séparé. La gestion des utilisateurs individuels et des permissions internes au cabinet reste nécessaire avant un déploiement partagé.

Le chat n'exécute ni actions ni instructions contenues dans les documents. Le frontend affiche du texte échappé, jamais du HTML fourni par le modèle. Les requêtes du navigateur passent par une session HttpOnly/SameSite et un contrôle d'origine.

## Structure

```text
frontend/app/                 Next.js, React, TypeScript, interface FR/AR
backend/app/api/routes/       documents, dossiers, recherche, chat
backend/app/models/           textes originaux, versions, projections de recherche, dossiers, audit
backend/app/services/
  ingestion/                  PDF, articles, paragraphes, dates
  embeddings/                 EmbeddingProvider et Ollama
  retrieval/                  lexique, projection, FTS + vecteurs + fusion
  reranking/                  abstraction et classement lexical
  llm/                        LLMProvider, adaptateurs et prompt juridique
  citations/                  vérification des références et extraits
  rag/                        orchestration et réponse contrôlée
backend/alembic/              migrations additives 0001 à 0004
backend/tests/                données exclusivement TEST LAW / TEST ARTICLE
```

## API principale

| Route | Fonction |
|---|---|
| POST `/legal-documents/upload` | PDF + métadonnées JSON multipart |
| POST `/legal-documents/ingest` | Extraction et découpage |
| GET `/legal-documents/{id}/articles` | Articles et filtre exact |
| POST/GET `/cases` | Création/liste des dossiers |
| POST/GET `/cases/{id}/documents` | Pièces du dossier |
| GET `/cases/{id}/timeline` | Mentions de dates à vérifier |
| POST `/search/legal`, `/search/case` | Recherche filtrée |
| POST `/search/index?batch_size=8` | Projection et embeddings par lot |
| POST `/rag/query` | Chat avec sources |
| POST `/cases/{id}/ask` | Question sur dossier + droit |
| GET `/assistant/status` | Disponibilité/configuration sans secrets |

Exemple de requête :

```json
{"question":"Quels textes encadrent la saisie conservatoire ?","scope":"LEGAL_ONLY","language":"fr","mode":"LEGAL_RESEARCH","top_k":8}
```

Les autres modes sont QUICK_ANSWER, CASE_ANALYSIS, DOCUMENT_ANALYSIS, DRAFT_PREPARATION, CASE_TIMELINE et COMPARE_ARGUMENTS. Ils orientent le prompt ; DRAFT_PREPARATION n'exporte pas de document judiciaire final. La chronologie structurée est accessible via l'endpoint dédié.

## Tests et migrations

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
docker compose --profile test run --build --rm tests
```

Le premier groupe utilise SQLite et des fournisseurs simulés pour les cas adverses. Le second vérifie également PostgreSQL réel, la RLS et la recherche vectorielle filtrée avec des vecteurs synthétiques. Il crée ses propres tenants TEST et nettoie uniquement ses données. Les dépendances backend sont figées dans les fichiers lock. Le frontend est compilé et vérifié par TypeScript lors du build Docker.

Le service `migrate` applique les migrations avant le démarrage du backend ; `Exited (0)` est normal. Les schémas historiques sont figés dans `migration_v1.py` et `migration_v2.py`. Les données existantes sont conservées, les projections sont ajoutées progressivement à la recherche ou à l'indexation. Ne pas effacer les volumes pour une mise à jour.

## Limites actuelles

- Extraction textuelle seulement ; scans et encodages cassés demandent OCR ou un autre PDF.
- Découpage automatique à vérifier (notes, sommaires et annexes peuvent créer des ambiguïtés).
- Reranker lexical de repli ; qualité sémantique et juridique à évaluer sur un corpus annoté.
- Pas de garantie d'exhaustivité, d'actualité des lois, d'entailment des citations ou de bonne interprétation.
- Le chat et les brouillons sont bornés ; les analyses complètes passent par le worker et une file persistante. Les conversations ne sont pas enregistrées.
- Pas de permissions individuelles, de validation éditoriale complète des lois, d’export DOCX, de mémoire transversale du cabinet activée ni de mises à jour automatiques du JORT.
- Sauvegarder ensemble PostgreSQL, les PDF et `.env` dans un emplacement sécurisé.

Le détail du socle documentaire précédent est conservé dans [docs/PHASES_1_4.md](docs/PHASES_1_4.md). Les rapports d'exécution indiquent précisément les vérifications réalisées.

Références : [Ollama chat](https://docs.ollama.com/api/chat), [embeddings](https://docs.ollama.com/api/embed), [BGE-M3](https://ollama.com/library/bge-m3), [Next.js](https://nextjs.org/docs/app/getting-started/installation), [PostgreSQL RLS](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).
