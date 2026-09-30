# Validation de la recherche exacte de reference

Correction limitee au mode exact de reference juridique.

## Architecture ajoutee

- `ExactReferenceParser` detecte une reference explicite d'article ou de الفصل.
- Chaque match converge vers `ExactLegalReference` avec numero normalise, suffixe, document, `document_id` resolu et offsets.
- Le parser retourne une liste dedupliquee et choisit `EXACT_REFERENCE_QUERY` ou `MULTI_EXACT_REFERENCE_QUERY` selon le nombre de references uniques.
- `QueryRouter` separe `EXACT_REFERENCE_QUERY` de `CONCEPTUAL_LEGAL_SEARCH`, `CASE_ANALYSIS` et `DOCUMENT_ANALYSIS`.
- `normalize_article_number()` conserve la valeur brute et produit un numero normalise unique pour le parser, le repository et les tests.
- `normalize_document_reference()` normalise les titres et alias sans modifier leur texte original.
- `LegalDocumentResolver` resout les titres et les alias configurables vers un `document_id`.
- `LegalDocumentAlias` stocke les alias normalises, leur langue et leur type.
- `LegalRepository.exact_article_reference()` applique la meme normalisation aux references stockees avant le lookup exact.
- `ExactReferenceService` deduplique les lignes par identite canonique : `document_id`, reference d'article normalisee, suffixe et `version_id`.
- `SourceIdentityValidator` verifie la coherence `document_id`, `article_id`, `article_number`, `chunk_id`, pages et texte.
- `ExactReferenceService` formate les statuts deterministes : `FOUND`, `PARTIAL_EXACT_RESULTS`, `ARTICLE_NOT_FOUND`, `DOCUMENT_NOT_FOUND`, `AMBIGUOUS_REFERENCE`, `AMBIGUOUS_REFERENCE_MAPPING`, `AMBIGUOUS_VERSION`, `INVALID_REFERENCE_QUERY`, `SOURCE_IDENTITY_MISMATCH`.

## Chemin d'execution

Pour une `EXACT_REFERENCE_QUERY`, les routes `/search/legal` et `/rag/query` sortent avant le pipeline RAG classique :

- aucun semantic search ;
- aucun hybrid search ;
- aucun reranker ;
- aucun LLM ;
- aucun fallback silencieux ;
- aucun article similaire.

Le debug expose :

```json
{
  "raw_query": "article TEST_ARTICLE_X dans TEST_CODE_A",
  "query_type": "EXACT_REFERENCE_QUERY",
  "raw_document_reference": "TEST_CODE_A",
  "normalized_document_reference": "test code a",
  "raw_article_reference": "TEST_ARTICLE_X",
  "normalized_article_reference": "test_article_x",
  "normalized_article_number": "test_article_x",
  "requested_document": "TEST_CODE_A",
  "resolved_document_id": "uuid",
  "matched_alias_id": "uuid-or-null",
  "parser_confidence": 1.0,
  "resolver_status": "FOUND",
  "rows_found": 1,
  "raw_rows_found": 1,
  "unique_legal_article_ids": ["uuid"],
  "canonical_articles_found": 1,
  "chunks_found": 1,
  "duplicates_removed": 0,
  "duplicate_rows_removed": 0,
  "chunk_groups_found": 1,
  "versions_found": 1,
  "final_status": "FOUND",
  "returned_article_ids": ["uuid"],
  "semantic_search_called": false,
  "hybrid_search_called": false,
  "reranker_called": false,
  "llm_called": false,
  "lookup_strategy": "legal_articles_exact"
}
```

## Scenarios testes

- reference exacte existante : un seul resultat ;
- reference absente : `ARTICLE_NOT_FOUND` ;
- meme numero dans deux documents avec document precise : document demande uniquement ;
- meme numero dans deux documents sans document precise : `AMBIGUOUS_REFERENCE` ;
- requete conceptuelle sans numero : reste `CONCEPTUAL_LEGAL_SEARCH` ;
- variantes Unicode et suffixes : reference resolue sans confondre article simple et article suffixe ;
- representation numerique differente : meme article exact ;
- article canonique avec trois chunks : `FOUND`, une seule source ;
- doublons exacts issus de l'ingestion : un seul article canonique apres deduplication ;
- formulations francaise, arabe et mixte avec contexte : meme identite canonique ;
- alias de document configure : meme `document_id` canonique ;
- alias utilise : `matched_alias_id` expose dans le debug ;
- ordre des mots, contexte et ponctuation variables : meme identite canonique ;
- texte technique ou pseudo-code : ne declenche pas une recherche exacte ;
- references multiples valides : un lookup exact par reference, sans arret apres le premier resultat ;
- syntaxe courte alias de document + numero : recherche exacte prioritaire ;
- parenthese ou crochet autour du numero : meme reference canonique ;
- document explicite inconnu : `DOCUMENT_NOT_FOUND` ;
- association article/document indecidable : `AMBIGUOUS_REFERENCE_MAPPING` ;
- syntaxe manifestement malformee : `INVALID_REFERENCE_QUERY` ;
- meme article dans deux versions d'une serie : `AMBIGUOUS_VERSION` ;
- numero sans document et sans correspondance : `ARTICLE_NOT_FOUND` apres lookup global ;
- demande des donnees stockees : une source exacte, metadonnees issues de la base ;
- incoherence source/chunk : `SOURCE_IDENTITY_MISMATCH` ;
- filtre corpus : applique dans la requete SQL exacte.

## Comptes de sources

| Scenario | Statut | Sources retournees | Search/Rerank/LLM |
|---|---|---:|---|
| Reference exacte existante | `FOUND` | 1 | false / false / false |
| Reference exacte absente | `ARTICLE_NOT_FOUND` | 0 | false / false / false |
| Reference exacte ambigue | `AMBIGUOUS_REFERENCE` | 0 | false / false / false |
| Versions exactes ambigues | `AMBIGUOUS_VERSION` | 0 | false / false / false |
| Document impose | `FOUND` | 1, uniquement ce document | false / false / false |
| Article avec trois chunks | `FOUND` | 1 | false / false / false |
| Doublon exact d'ingestion | `FOUND` | 1 | false / false / false |
| Donnees stockees | `FOUND` | 1 | false / false / false |

## Validation

Commande locale :

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
```

Resultat :

```text
123 passed, 2 skipped, 2 warnings
```

Validation Docker complete de cette etape :

```powershell
docker compose run --build --rm tests
```

Resultat Docker :

```text
125 passed, 2 warnings
```

La migration Alembic `0007` est appliquee et la table `legal_document_aliases` est presente dans PostgreSQL. Le backend et le worker reconstruits sont actifs.
