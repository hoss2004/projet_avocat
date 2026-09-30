# Assistant juridique — phase 1

Cette phase transforme la route `/rag/query` en orchestration juridique fondée sur les preuves, tout en conservant `ExactReferenceService` et le moteur hybride existants.

## Pipeline livré

```text
RagRequest
  -> LegalIntentRouter
       -> primary_intent
       -> explicit_references[]
  -> LegalIssueAnalyzer
       -> LegalIssue[]
  -> EvidenceRetriever (une recherche par LegalIssue)
       -> ExactReferenceService pour les références obligatoires
       -> moteur hybride existant
       -> RetrievalQualityGate
  -> LegalEvidencePack
  -> LegalReasoningService (LLM seulement ici)
  -> ClaimSourceValidator
  -> réponse structurée + sources originales
```

Une demande de simple consultation comme `article 12` reste une `EXACT_REFERENCE_QUERY`. Une demande telle que `Donne un avis juridique au regard de l'article 12` devient `LEGAL_OPINION`; l'article 12 est alors une preuve obligatoire du paquet.

## Objets Pydantic

Les modèles sont définis dans `app/services/legal_assistant/models.py` :

- `LegalUserRequest` sépare `primary_intent`, `explicit_references`, résultat demandé, langue, dossier, documents et contraintes.
- `LegalIssue` contient sa question, ses requêtes de recherche, ses références et son propre `evidence_status` (`SUPPORTED`, `PARTIAL`, `NO_RELIABLE_SOURCE`).
- `LegalEvidence` conserve le texte original, le type de source, la page, la hiérarchie documentaire et les questions auxquelles la preuve est reliée.
- `LegalEvidencePack` range séparément textes, jurisprudence, pièces du dossier, références explicites et informations manquantes. Les champs faits et chronologie sont déjà prévus pour la phase suivante.
- `ClaimSupport` expose `SUPPORTED`, `PARTIALLY_SUPPORTED`, `UNSUPPORTED`, `CONTRADICTED` ou `NO_SOURCE`.

## Intervention du LLM

Le LLM intervient uniquement dans `LegalReasoningService`, après le routing, la décomposition, le retrieval et le contrôle qualité. Le fournisseur intégré reçoit le `LegalEvidencePack` structuré et le prompt séparé `legal_reasoning_prompt.py`. Un second appel indépendant vérifie le soutien de chaque affirmation. Seules les affirmations `SUPPORTED` sont affichées comme analyse; si aucune ne passe, l'interface revient aux extraits documentaires.

Le LLM ne choisit pas et ne fabrique pas l'identité des sources. Les identifiants, titres, numéros d'articles, pages, URL, versions et textes originaux viennent de la base et du retrieval. Il ne peut pas inventer des faits, dates, montants, délais, jurisprudences ou dispositions. Une question sans preuve suffisante est marquée `NO_RELIABLE_SOURCE` et peut seulement signaler l'information manquante.

## API et interface

La réponse de `/rag/query` ajoute `primary_intent`, `legal_user_request`, `legal_issues`, `evidence_pack`, `reasoning`, `claim_validation` et `answer_kind`. Les anciens champs (`answer`, `mode`, `claims`, `retrieved_sources`, `warnings`, `confidence`) restent disponibles.

L'interface propose maintenant les modes avis juridique, argumentation, analyse procédurale et brouillon. Elle affiche les questions identifiées avec leur état de preuve, l'analyse générée et les sources originales dans des zones distinctes.

## Portée suivante

`LegalDraftingService`, l'extraction détaillée des faits et contradictions, la jurisprudence avancée et la mémoire complète du Case Workspace restent les prochaines étapes. Le type d'intention `LEGAL_DRAFTING` est déjà routé, mais aucun gabarit d'acte spécialisé n'est encore appliqué.
