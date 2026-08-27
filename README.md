# Archers Agathois — données résultats FFTA

Ce dépôt collecte automatiquement les résultats publics officiels FFTA utiles au site de la Compagnie des Archers Agathois.

## Fonctionnement

Toutes les 6 heures, GitHub Actions :

1. consulte le calendrier public FFTA sur une période récente ;
2. repère les liens **Résultats** officiels hébergés sur `extranet.ffta.fr` ;
3. télécharge uniquement les PDF qui n'ont pas déjà été analysés ;
4. recherche les licences définies dans `config/archers.json` ;
5. extrait les résultats trouvés dans `data/results.json` ;
6. mémorise les PDF déjà analysés dans `data/processed.json`.

Un PDF qui provoque une erreur n'est pas marqué comme traité : il sera retenté au passage suivant.

## Exécution manuelle

Dans GitHub : **Actions → Collecte résultats FFTA → Run workflow**.

Pour le premier test, utiliser `60` jours.

## JSON public pour WordPress

`https://raw.githubusercontent.com/LoycD/archers-agathois-data/main/data/results.json`

## Rôle des sources dans le futur plugin WordPress

- **FFTA** : résultats officiels récents, scores, séries, concours, lieux et places.
- **Arc Occitanie** : classement régional Occitanie et historique complémentaire.
- Le plugin fusionnera les deux sources sans créer de doublons.

## Remarque technique

Le collecteur s'appuie sur les PDF officiels reliés depuis le calendrier FFTA. Il ne devine pas les URL des résultats et ne nécessite aucun identifiant privé.
