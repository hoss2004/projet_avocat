# Analyse complète des dossiers

Dans **Dossiers**, créer une affaire (nom/référence facultatifs), identifier si possible client et adversaire, déposer plusieurs pièces puis cliquer **Analyser entièrement le dossier**. Le chat reconnaît aussi « Analyse cette القضية » lorsque le dossier est sélectionné.

Le worker persistant parcourt tous les passages autorisés du dossier. Il conserve les extractions par pièce, avec une clé dépendant du checksum, de la version du pipeline, du modèle et des identités renseignées. Un nouveau document déclenche une mise à jour si une analyse existe déjà. Les pièces inchangées sont réutilisées. Un ajout pendant une analyse déclenche une nouvelle passe après la passe courante.

## Rapport et provenance

Le rapport contient 24 rubriques : fiche, résumé, parties, objet, chronologie, documents, faits, demandes, arguments, questions juridiques, textes, analyse, procédure, preuves, contradictions, éléments manquants, questions client et sources. Une matrice faits/droit ne reçoit une application que si les citations incluent à la fois une pièce et une source juridique.

Chaque extraction comporte une source et un statut. Les affirmations sont des allégations ou des observations non vérifiées. Le bouton **Valider ce fait comme établi** enregistre une décision explicite du cabinet ; l’IA ne donne pas ce statut automatiquement. Les déductions juridiques sont identifiées comme provisoires. Les pièces favorables/défavorables sont des candidates déterminées par leur utilisation dans les arguments, pas une mesure de force probante.

Les conflits de dates sont des **contradictions possibles**, regroupées par catégorie d’événement ; deux événements distincts peuvent expliquer des dates différentes. La présence d’une affirmation de paiement sans justificatif identifié crée une question, jamais une conclusion de non-paiement. Les documents suggérés ne sont pas déclarés obligatoires.

Formats : PDF, TXT UTF-8 et EML avec corps texte. Les PDF sans texte sont conservés et signalés comme illisibles/OCR nécessaire. Aucun OCR automatique n’est ajouté. Les pages TXT/EML sont des pages logiques. La date du document ou son auteur peuvent rester inconnus.

## Brouillons

Après analyse, choisir un des douze types : requête, conclusions, réponse adverse, mémoire, consultation, mise en demeure, courrier client, résumé, note interne, chronologie, liste de pièces ou préparation d’audience. Les exports sont en **Markdown**, pas DOCX. Les résumés, chronologies et listes peuvent être assemblés à partir du rapport sans nouvelle interprétation. Une génération rejetée est remplacée par une trame documentaire sourcée et explicitement signalée. Aucun acte n’est envoyé, signé ou déposé.

La rédaction reçoit un contexte borné et équilibré entre pièces et lois. Le nombre de sources disponibles et de sources effectivement fournies est conservé dans le résultat ; elle ne constitue pas une rédaction exhaustive garantie d’un long dossier. Les rubriques non établies restent vides avec une explication.

## Exécution et sécurité

- `worker` : file persistante PostgreSQL, traitement par dossier et reprise d’une tâche dont le bail a expiré après dix minutes. Chaque pièce est enregistrée au fil du traitement.
- Les tables d’analyse, de brouillons et de vocabulaire appliquent la RLS du cabinet. Tous les accès contrôlent aussi le dossier.
- Les analyses courtes du chat peuvent utiliser les questions juridiques mémorisées d’un rapport à jour ; pas de nouvelle lecture de toutes les pièces à chaque question.
- La mémoire transversale du cabinet reste désactivée : aucun ancien dossier d’un autre client n’est ajouté automatiquement.

## API

| Route | Usage |
|---|---|
| POST `/cases/{id}/analyze` | Mettre une analyse en file, réponse 202 |
| GET `/cases/{id}/analysis` | Dernier état et rapport ; `summary=true` pour un suivi léger |
| GET `/cases/{id}/analyses/{analysis_id}` | Rapport autorisé |
| GET `/cases/{id}/analyses/{analysis_id}/export` | Export Markdown |
| POST `/cases/{id}/facts/{fact_id}/review` | Validation explicite : established, disputed, unverified |
| POST `/cases/{id}/analyses/{analysis_id}/drafts` | Brouillon du type sélectionné |
| GET `/cases/{id}/drafts` | Brouillons conservés |

## Limites de validation

Le test navigateur porte sur un petit dossier synthétique, pas sur cent pages réelles. Il vérifie le dépôt multiple, le worker, le rapport, le téléchargement d’un brouillon et le mobile. Le temps mesuré figure dans `case-browser-check.json` ; il ne permet pas de promettre quelques minutes pour tout dossier. La vitesse dépend notamment du nombre de passages, du GPU et du modèle.

Le classifieur documentaire et Qwen 2.5 3B peuvent se tromper sur des catégories, des rôles ou l’interprétation. Les citations exactes ne constituent pas une preuve d’implication logique. Le contrôle humain reste nécessaire pour parties, demandes, analyse adverse, pièces manquantes et conclusions. L’absence d’une rubrique remplie n’établit pas l’absence juridique du point concerné.
