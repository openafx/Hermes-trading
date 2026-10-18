# Déploiement additif dans le projet Railway existant

## Accès manquant

Aucun accès authentifié au projet Railway ni au dépôt GitHub de l’utilisateur n’était disponible lors de la construction. Aucun service distant n’a été modifié. Il faut identifier le projet/service Hermes, son dépôt et son répertoire de travail avant d’appliquer cette procédure. Ne jamais remplacer l’installation par la copie du dépôt public NousResearch.

## Préservation et préparation

1. Relever service, commit, commande de démarrage, chemins persistants et variables **sans exporter les secrets dans les journaux**.
2. Sauvegarder l’historique Hermes, la configuration et le volume avec la méthode de sauvegarde existante. Pour une base SQLite active, utiliser l’API de backup SQLite ou arrêter proprement le service ; copier seulement le fichier `.db` peut perdre le WAL.
3. Ajouter l’extension avec `install_into_existing.py`, sans changer le service Hermes. Committer les nouveaux fichiers dans le dépôt privé existant après revue.
4. Créer un service Railway séparé, même projet et même environnement. Racine : `Hermes-trading/extensions/prop-connect-v0.2` (adapter au vrai chemin dans le dépôt). Utiliser son Dockerfile et son `railway.json`.
5. Créer **un volume dédié**, monté sur `/data`. Une réplique unique pour cette version SQLite ; pas de mise à l’échelle horizontale. Le volume Hermes n’est pas partagé ni remplacé.

## Variables du nouveau service

| Variable | Valeur / fonction |
|---|---|
| `PROP_ADMIN_TOKEN` | Secret aléatoire >=32 caractères ; même référence dans l’environnement du client MCP Hermes |
| `PROP_DATA_DIR` | `/data/prop-connect` |
| `PROP_PUBLIC_BASE_URL` | Origine HTTPS exacte du nouveau service, sans chemin |
| `PROP_HERMES_API_URL` | Origine du gateway API Hermes existant, réseau privé si possible |
| `PROP_HERMES_API_TOKEN` | Secret du gateway existant si requis |
| `PROP_RESEARCH_ENABLED` | `false` au départ ; `true` permet les recherches payantes |
| `PROP_HERMES_READONLY_PROFILE_CONFIRMED` | `false` jusqu’à vérification réelle d’un profil Hermes privé des outils d’exécution |
| `PROP_OPENBB_ALLOWED_TOOLS` | Liste exacte, séparée par virgules, des outils de données autorisés ; vide = découverte seule |
| `PROP_TRADOVATE_DEMO_TOKEN` | Facultatif : token DEMO avec permissions lecture, pas un token live |
| `PROP_TRADOVATE_ACCOUNT_ID` | Compte DEMO précis obligatoire pour sonder Tradovate |

`PORT` est fourni par Railway. Les services MCP intégrés restent sur 127.0.0.1:8901 et :8902 ; ne pas publier ces ports. Les endpoints MCP distants optionnels `PROP_OPENBB_MCP_URL` / `PROP_VIBE_MCP_URL` remplacent les services locaux et peuvent utiliser leurs `PROP_*_MCP_TOKEN`. Le lancement intégré ne transmet pas les secrets du gateway, du broker ou des LLM aux services de données. Les fournisseurs nécessitant des clés demanderont une configuration dédiée et une validation supplémentaire.

Le Dockerfile installe par défaut tous les environnements de recherche ; il peut produire une image volumineuse et un build long. Pour une image plus légère, modifier l’argument `INSTALL_PROFILES` après choix explicite des modules nécessaires. Les coûts Railway/LLM/données n’ont pas été estimés à partir d’un compte réel. Pas de Docker disponible ici : build distant à vérifier avant promotion.

## Connecter le Hermes déjà installé

Sur le service existant, repérer sa vraie configuration. Générer une proposition distincte :

```bash
python scripts/configure_hermes.py --config /chemin/config.yaml --output /chemin/config.prop-connect.proposal.yaml --mcp-url https://EXTENSION.up.railway.app/mcp
```

La proposition conserve l’original, mais sa réécriture YAML peut modifier les commentaires/mise en forme. Examiner le diff, ne fusionner que `mcp_servers.prop_connect`. Vérifier que la version Hermes en production accepte ces champs (trust, tools.include), puis recharger MCP avec le mécanisme supporté par cette version. `PROP_ADMIN_TOKEN` doit exister dans l’environnement Hermes ; la proposition contient seulement `${PROP_ADMIN_TOKEN}`. Le profil doit interdire les outils shell/broker/deploiement si l’on veut garantir la recherche seule : un prompt n’est pas une barrière de permissions.

Les 7 outils ajoutés permettent d’appeler le rapport, les sources et la file de tâches. Ils ne remplacent pas la boucle d’agent Hermes. Éviter toute automatisation récursive Hermes → tâche hermes_review → Hermes.

## Historique et recette

L’ancien historique ne peut pas être migré sans son schéma. Utiliser un export de transactions **fermées**, des IDs stables et des dates avec fuseau. Préparer un mapping JSON ; vérifier la prévisualisation puis importer via l’UI ou `scripts/import_existing_history.py --help`. Un journal par compte. Le fichier source reste intact. Ne pas confondre fills bruts et transactions fermées.

Recette requise : healthcheck, login, refus sans token, découverte des 7 outils depuis Hermes, sonde Hermes, sondes MCP OpenBB/Vibe et lecture d’un skill, probes des 5 moteurs, import d’un petit export réel réconcilié, redémarrage puis persistance journal/tâches, sauvegarde et restauration en environnement de test. Vérifier que les données fictives restent hors du journal réel. Les résultats historiques ne prouvent ni rentabilité future ni conformité intratrade.

## Retour arrière

Désactiver seulement `mcp_servers.prop_connect`, recharger Hermes, arrêter le nouveau service et conserver son volume pour récupération. Ne pas supprimer le volume Hermes. Revenir au commit précédent de l’extension si nécessaire. Aucune reprise automatique des tâches interrompues, pour éviter des appels LLM facturés deux fois. Aucun ordre de trading n’est disponible dans cette extension.

## Limites opérationnelles

Un seul administrateur, pas de rôles multiutilisateurs. Le cookie expire en 8 heures ; changer le secret révoque les sessions. L’accès API/MCP utilise le même secret administrateur : le limiter aux clients de confiance. La sonde `/healthz` prouve uniquement le démarrage du gateway, pas la disponibilité des services MCP. Les jobs tracent les erreurs par type pour éviter la fuite de secrets, mais ne fournissent pas une observabilité de production complète. Import maximal 8 Mo, résultats de recherche limités, une tâche à la fois.
