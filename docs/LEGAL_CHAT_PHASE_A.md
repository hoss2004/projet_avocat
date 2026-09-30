# Legal AI conversationnel — Phase A

## Point d'entrée

`LegalChatOrchestrator` intervient avant le retrieval pour toutes les demandes non limitées à l'affichage évident d'une référence. Le modèle produit un `UserTask` contenant un objectif principal, des objectifs secondaires, les questions indépendantes, les transformations demandées, les documents simplement mentionnés et un plan d'appels aux outils.

```text
Message + historique utile + mode + dossier sélectionné
  -> extraction déterministe des références (garde-fou)
  -> LLM LegalChatOrchestrator (compréhension et plan seulement)
  -> UserTask multi-objectifs
  -> tool calls nécessaires
  -> EvidencePack
  -> LLM de raisonnement et rédaction
  -> ClaimSourceValidator
  -> réponse conversationnelle + panneau de sources
```

Le premier appel LLM ne produit aucune règle juridique. Il ne fait que planifier. Les règles déterministes restent utilisées pour les cas évidents et comme repli si le modèle de planification est indisponible.

## RAG comme outil

Les outils internes déclarés sont : `search_legal_sources`, `lookup_exact_reference`, `search_case_law`, `search_case_documents`, `read_document`, `search_current_case` et `get_active_draft`. La Phase A exécute les recherches juridiques, références exactes et recherches de pièces à travers le moteur existant. Une demande purement conversationnelle peut produire zéro appel de recherche.

Une référence explicite reste toujours séparée de l'objectif. Par exemple, un avis fondé sur un article garde `primary_goal=LEGAL_OPINION` et ajoute `lookup_exact_reference` au plan.

## Réponse sans source fiable

La réponse n'est plus remplacée par une liste de passages. Le modèle peut produire :

- `SOURCE_BACKED` pour une règle précise soutenue par une citation originale ;
- `USER_PROVIDED_FACT` pour un fait explicitement présenté comme raconté ou non vérifié ;
- `GENERAL_REASONING` pour une méthode, un argument possible ou une analyse générale sans référence positive inventée ;
- `MISSING_INFORMATION` pour une pièce, un fait ou une règle à obtenir.

Une affirmation `GENERAL_REASONING` contenant un numéro d'article, une référence officielle, une durée ou un montant précis sans source est rejetée. Une affirmation `SOURCE_BACKED` sans citation est également rejetée.

## Debug

Avec `debug=true`, `/rag/query` expose : `primary_goal`, `secondary_goals`, `llm_orchestrator_called`, `tool_calls`, `sources_selected_count`, `sources_rejected_count`, `reasoning_llm_called`, `draft_created`, `draft_id`, `claim_validation_called` et `final_response_type`.

`draft_created` reste `false` dans cette phase. La persistance, le versionnement et la modification conversationnelle du draft appartiennent à la Phase D, après la mémoire structurée des Phases C et D.
