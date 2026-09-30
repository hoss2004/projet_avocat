# Validation du retrieval juridique

Le retrieval ne repose plus sur un simple score vectoriel. Le pipeline applique maintenant :

- analyse de la question juridique ;
- expansion francais/arabe ;
- recherche hybride : vectoriel, lexical, full text, arabe normalise, reference exacte d'article ;
- filtrage par corpus et droits d'acces ;
- boost domaine, sous-domaine et section ;
- reranking juridique ;
- seuil minimal de pertinence ;
- quality gate avant generation ;
- validation claim-source apres generation.

## Structure juridique

Chaque article peut porter les metadonnees suivantes :

- code ;
- livre ;
- titre ;
- chapitre ;
- section ;
- sous-section ;
- domaine juridique ;
- sous-domaine juridique ;
- texte juridique d'origine pour les PDFs composites ;
- texte original, texte normalise et texte de recherche.

La commande `python -m app.rebuild_legal_metadata` reconstruit ces metadonnees sans supprimer les articles ni les embeddings existants.

## Procedure civile

Question : `Un tiers affirme etre proprietaire d'un bien saisi. Que peut-il faire ?`

| Rang | Avant | Apres | Score |
|---:|---|---|---:|
| 1 | Code de la fiscalite locale, article 3 | Code de procedure civile et commerciale, article 403 | 7.720573 |
| 2 | Code de la fiscalite locale, article 17 bis | Code de procedure civile et commerciale, article 370 | 5.462111 |
| 3 | Code TVA, article 5 | Code de procedure civile et commerciale, article 365 | 5.460305 |

Le domaine detecte est `civil_procedure`, avec priorite sur l'execution/saisie. Le test arabe classe aussi l'article `403` en premier.

## Bail et loyers impayes

Question longue : `La societe BETA met ALPHA en demeure de payer trois mois de loyers impayes et menace de demander la resiliation du contrat de location.`

| Rang | Article | Sous-domaine | Score |
|---:|---:|---|---:|
| 1 | 796 | lease | 10.737635 |
| 2 | 755 | lease | 10.717084 |
| 3 | 793 | lease | 10.700537 |

Question courte : `paiement de loyers impayes et resiliation du bail`

| Rang | Article | Sous-domaine | Score |
|---:|---:|---|---:|
| 1 | 796 | lease | 10.737635 |
| 2 | 755 | lease | 10.724836 |
| 3 | 802 | lease | 10.710462 |

Question arabe : loyers impayes et rupture du bail.

| Rang | Article | Sous-domaine | Score |
|---:|---:|---|---:|
| 1 | 755 | lease | 10.724345 |
| 2 | 741 | lease | 10.72304 |
| 3 | 756 | lease | 10.714435 |

Les resultats ne forcent plus `top_k` : `top_k` est un maximum. Les resultats insuffisants sont filtres.

## Generation controlee

Le RAG sur le bail a conserve trois sources : articles `796`, `755`, `802`, toutes en `lease`. La reponse generee n'est acceptee que si les citations soutiennent reellement les affirmations.

Les citations trop faibles, comme un simple numero d'article ou un court en-tete, sont rejetees. Les nombres, dates, delais, montants, pourcentages, sanctions et durees doivent apparaitre dans les citations citees pour etre acceptes.
