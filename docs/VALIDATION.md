# Validation finale de l'assistant local

Verification realisee le 20 septembre 2026 sur l'environnement local Docker.

## Etat deploye

- Backend, worker et frontend reconstruits avec `docker compose up -d --build backend worker frontend`.
- Migrations Alembic appliquees jusqu'a `0006_composite_texts`.
- Services actifs : backend `127.0.0.1:8000`, frontend `127.0.0.1:3000`, Postgres sain, worker lance.
- Ollama/Qwen utilise par le backend local quand le modele repond.

## Tests

- Tests locaux : `81 passed, 2 skipped`.
- Tests Docker : `83 passed`.
- Controle navigateur Edge headless : bibliotheque visible, compteurs d'index visibles, pas d'erreur JavaScript, pas de debordement mobile.

## Index juridique

- Documents : `18`.
- Articles indexes : `4829`.
- Embeddings : `4829`.
- Reindexation des metadonnees : `4829` articles mis a jour, `0` article perdu.
- Les corpus restent separes : droit tunisien, jurisprudence, documents de dossier, memoire cabinet non recherchee.
- Les documents scannes restent marques OCR requis et ne sont pas presentes comme fiables pour la recherche.

## Retrieval corrige

Question procedure civile : `Un tiers affirme etre proprietaire d'un bien saisi. Que peut-il faire ?`

- Resultat attendu : article `403` classe premier.
- Validation generale : `403`, puis `370`, puis `365`.
- Test arabe : article `403` classe premier.

Question bail longue : `La societe BETA met ALPHA en demeure de payer trois mois de loyers impayes et menace de demander la resiliation du contrat de location.`

- Resultats francais : articles `796`, `755`, `793`, tous en sous-domaine `lease`.
- Question courte : articles `796`, `755`, `802`, tous en sous-domaine `lease`.
- Question arabe loyers/rupture du bail : premiers resultats `755`, `741`, `756`, tous en sous-domaine `lease`.

## Generation RAG

Le endpoint reel `/rag/query` a ete teste sur la question du bail.

- Mode : `generated`.
- Sources conservees : articles `796`, `755`, `802`, toutes en `lease`.
- Les citations faibles du type numero d'article seul sont rejetees par le validateur.
- Les affirmations generees doivent etre supportees par des citations substantielles ; sinon le systeme revient aux extraits sans conclusion automatique.

## Fichiers de preuve

- `docs/retrieval-after.json`
- `docs/retrieval-arabic-check.json`
- `docs/lease-after.json`
- `docs/lease-short.json`
- `docs/lease-arabic.json`
- `docs/lease-rag-validation.json`
- `docs/index-structure-status.json`
- `docs/structure-browser-check.json`
- `docs/structure-integrity.json`

## Limites restantes

Les resultats restent des validations techniques, pas une certification juridique. Les documents composites et les textes marques `unidentified` sont bloques ou signales avant generation et demandent une verification humaine. Les PDFs scannes demandent un OCR avant d'etre exploitables.
