# Legal AI conversationnel

Le point d'entrée utilisateur reste `POST /rag/query`, mais chaque tour est désormais traité comme un message d'une conversation persistante. Le client envoie le `conversation_id` reçu au premier tour. Le serveur sélectionne les derniers tours utiles, le `CaseState` actif et le `LegalDraft` actif; il n'injecte pas tout l'historique brut.

## Pipeline

1. `ConversationMemory` charge ou crée la conversation et le dossier actif.
2. `LegalConversationAgent` est le point d'entrée conversationnel principal. Il délègue la planification interne à `LegalConversationOrchestrator`, produit un `UserTask`, choisit les outils et applique le budget `MAX_TOOL_CALLS`.
3. Les outils de recherche exécutent les questions séparément. Un second cycle est autorisé pour une question complexe sans résultat fiable, dans la limite du budget.
4. Le LLM reçoit les faits structurés, les sources retenues et le brouillon utile.
5. `ClaimSourceValidator` contrôle les affirmations juridiques précises.
6. Un avis ou une rédaction crée un `LegalDraft` v1. Une correction ciblée crée une nouvelle `LegalDraftVersion`.
7. Le tour utilisateur et le tour assistant sont enregistrés, avec les identifiants du dossier et du brouillon.

`LegalConversationOrchestrator` produit aussi une `ConversationDecision` distincte du plan RAG : `intent`, `case_action`, `draft_action`, `retrieval_required`, `reasoning_required` et `response_mode`. Le message actuel a priorité sur le contexte. Les messages sociaux évidents sont protégés contre une mauvaise classification du modèle : même avec un brouillon actif, « bonjour », « merci », « ok » ou « parfait » restent `GENERAL_CHAT`.

## Mémoire persistante

- `CaseState` : parties, rôles, faits communiqués, allégations, faits contestés, chronologie, questions juridiques, documents, sources vérifiées et questions ouvertes.
- `ConversationState` : dossier et brouillon actifs, derniers tours sélectionnés, sujet courant, dernière section modifiée et questions en attente.
- `LegalDraft` : document actif, type, titre, contenu, sections, version et sources utilisées.
- `LegalDraftVersion` : photographie immuable de chaque création ou modification.

Toutes ces tables portent un `tenant_id` et utilisent la politique PostgreSQL RLS du projet.

## Opérations de rédaction

Les opérations reconnues sont `ADD`, `DELETE`, `REPLACE`, `REWRITE`, `EXPAND`, `SHORTEN`, `MOVE`, `MERGE`, `CHANGE_TONE`, `STRENGTHEN_ARGUMENT`, `SOFTEN_CONCLUSION`, `ADD_SOURCE`, `REMOVE_ARGUMENT`, `ADD_COUNTERARGUMENT` et `UPDATE_WITH_NEW_FACTS`.

Une opération stylistique charge le brouillon sans relancer le RAG. L'ajout d'une nouvelle référence déclenche la recherche nécessaire. Une révision qui introduit un numéro d'article absent du brouillon et des sources vérifiées est rejetée.

Pour une modification ciblée, le modèle reçoit le plan des sections et la seule section visée. Il retourne un patch de section; le backend conserve les autres sections mot pour mot et construit la version suivante. Une transformation explicitement globale, par exemple la conversion de l'avis en courrier au client, peut utiliser une réécriture complète.

La section cible est résolue sémantiquement à partir de son titre et de son contenu. L'utilisateur peut donc viser « la partie sur les biens sociaux » sans connaître le titre technique. Si le premier patch est invalide, le modèle reçoit le motif de validation et dispose d'une seconde tentative. Après deux échecs, l'API demande une précision en langage naturel et ne transmet jamais l'exception interne.

Chaque réponse expose aussi le contrat stable de l'agent : `user_facing_response`, `case_updates`, `draft_updates`, `sources_used`, `tool_calls` et `follow_up_needed`. Les anciens champs restent présents pour compatibilité avec le frontend. Les codes internes de retrieval et de patch sont transformés en messages utilisateur; les traces de debug conservent seulement un type d'échec non sensible.

En mode `CHAT`, le modèle ne reçoit ni le contenu du brouillon ni les anciennes sources. L'API retourne des listes vides pour les questions juridiques et les sources, et le frontend masque leur panneau. Le brouillon reste actif en mémoire sans devenir la réponse courante.

## API de contrôle

- `GET /conversations/{conversation_id}` : état et tours de la conversation.
- `GET /drafts/{draft_id}` : brouillon actif.
- `GET /drafts/{draft_id}/versions` : historique des versions.
- `GET /cases/{case_id}/state` : mémoire structurée du dossier.

## Validation

Le test `test_seven_turn_conversation_persists_case_and_versions_draft` couvre le scénario imposé : création de l'avis, développement ciblé, suppression, nouveau fait, réévaluation, mise à jour et transformation en courrier. Il vérifie 14 tours persistés et les versions v1 à v5 du même brouillon.

`test_required_eight_turn_legal_conversation` couvre le parcours conversationnel complet : salutation, avis, développement sémantique, argument absent, mise à jour du dossier sans modification du brouillon, intégration du fait, conclusion nuancée et remerciement. `test_long_form_legal_analysis_contains_full_reasoning_stages` vérifie une rédaction développée. `test_rag_source_is_used_for_analysis_and_cited` vérifie la sélection `USE`, le raisonnement à partir d'une source et sa citation.

`test_greeting_after_draft_never_loads_returns_or_modifies_draft` force volontairement le planificateur à classer « bonjour » comme une réécriture. La garde de tour le corrige en `GENERAL_CHAT` et vérifie qu'aucune recherche, analyse de question juridique, lecture ou modification du brouillon ne se produit. `test_duplicate_generated_sections_are_rendered_once` trace `generated_sections` et `rendered_sections` et empêche l'affichage répété d'un même titre.
