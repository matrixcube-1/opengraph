# Politique de sécurité

## Périmètre

Ce dépôt contient une stratégie de trading propriétaire, ses paramètres
calibrés et sa méthodologie. La confidentialité du contenu fait partie du
modèle de risque économique du projet.

## Règles de base

- **Aucun secret dans le dépôt** : clés API, jetons, mots de passe, clés
  privées et credentials broker ne sont jamais commités. L'authentification
  machine passe par variables d'environnement (`ARB_HOST`, clés SSH hors
  dépôt) ou le gestionnaire de secrets de l'environnement d'exécution.
- Les accès venue (cTrader Open API) seront injectés à l'exécution
  uniquement — jamais dans la configuration versionnée.
- L'adresse du serveur de calcul est paramétrée (`$ARB_HOST`), pas codée en
  dur dans les documents versionnés.

## Signalement

Toute faille suspectée (fuite de secret dans l'historique, exposition de
données, vulnérabilité dans l'adapter venue) doit être remontée au
mainteneur du dépôt en privé — jamais en issue publique.

En cas de fuite d'un secret dans l'historique : révocation immédiate du
secret compromis, puis purge d'historique coordonnée.
