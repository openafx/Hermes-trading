# Étude d’intégration — 18 octobre 2026

## Décision d’architecture

Conserver le Hermes déjà en ligne comme orchestrateur. Ajouter une extension indépendante avec son propre stockage, une API/MCP, des tâches bornées et des environnements Python séparés. Les frameworks de recherche n’ont pas tous le même rôle et ne doivent pas partager un environnement de dépendances unique. Une collection de dépôts n’est pas une stratégie rentable.

Les 11 sources ont été récupérées et leurs SHA contrôlés localement. Elles ne sont pas toutes exécutées : le catalogue et InvestorSkills sont des documents ; le bot dearvn reste en quarantaine ; la copie publique de Hermes sert à l’étude des interfaces, pas au remplacement du service privé. Les packages runtime sont figés indépendamment dans `requirements-*.txt`.

| Dépôt | Utilité pour ce projet | Priorité | Intégration construite / preuve locale | Reste à valider |
|---|---|---|---|---|
| NousResearch/hermes-agent | Orchestrer et lire nos résultats | ★★★★★ | Client API et proposition MCP vers le Hermes existant | URL, authentification, version et profil réels |
| OpenBB-finance/OpenBB (source canonique dans le lock) | Données de marché et macro | ★★★★☆ | Serveur MCP isolé, découverte HTTP testée | Fournisseurs, frais, droits et qualité des données futures |
| questflowai/investorskills | Grilles d’analyse de recherche | ★★★☆☆ | Liste et lecture de SKILL.md sans les exécuter | Pertinence par stratégie, pas un signal automatique |
| TauricResearch/TradingAgents | Débat multiagents et rapport | ★★★☆☆ | Environnement 0.7.0 importé ; tâche de recherche câblée | Appels LLM, budget et qualité hors échantillon ; pas lancé avec des clés réelles |
| HKUDS/Vibe-Trading | Outils de recherche et analyse | ★★★☆☆ | MCP HTTP : découverte et lecture list_skills testées | Données/résultats métier ; aucun connecteur d’ordres activé |
| paperswithbacktest/awesome-systematic-trading | Catalogue de méthodes et composants | ★★★☆☆ | Lecture documentaire locale | Sélection, licences et reproduction des recherches |
| polakowo/vectorbt | Expérimentation rapide | ★★★★☆ | Benchmark synthétique MA avec signaux décalés, exécuté | Backtest futures et coûts réalistes non implémentés |
| edtechre/pybroker | Seconde implémentation de recherche | ★★★☆☆ | Benchmark synthétique de détention exécuté | Modèle de coûts/slippage et stratégie futures |
| ranaroussi/quantstats | Rapports sur séries de résultats | ★★★★☆ | Métriques de période exécutées sur données fictives | Calendrier complet, journées sans trade, réconciliation |
| nautechsystems/nautilus_trader | Moteur événementiel candidat | ★★★★☆ | Import 1.231.0 et sonde installés | Pas de stratégie, replay de marché ni adaptateur d’exécution intégrés |
| dearvn/tradovate-trading-bot | Référence technique Tradovate | ★☆☆☆☆ | Source épinglée, exécution bloquée | Audit requis ; préférer un adaptateur propre et testé |

Les étoiles expriment une priorité d’étude pour notre projet, ni les étoiles GitHub ni une probabilité de profit.

## Flux et frontières

Hermes appelle les 7 outils MCP du gateway. Le gateway lit le journal, consulte les documents, ou soumet une tâche persistante. OpenBB/Vibe sont appelés en HTTP MCP avec une liste de lectures autorisées. Les bibliothèques incompatibles tournent dans des processus séparés et reçoivent du JSON. QuantStats travaille sur les résultats importés ; vectorbt/PyBroker exécutent pour l’instant seulement des benchmarks techniques actions. Nautilus n’est encore qu’une sonde. TradingAgents et la revue via Hermes exigent l’activation explicite de la recherche payante.

L’extension ne connaît aucun endpoint de placement ou annulation d’ordres. Tradovate est limité à la sonde de compte DEMO, avec un compte explicitement configuré. Cela ne valide pas un token comme lecture seule : ses permissions doivent être contrôlées côté fournisseur. Les outils MCP que proposent les dépôts tiers ne sont pas automatiquement tous autorisés par notre gateway.

Les données ou textes de skills sont marqués comme non fiables ; ils ne donnent pas de permissions. Un profil Hermes avec outils shell/broker pourrait néanmoins agir en dehors de cette extension : confirmer réellement ses permissions avant d’autoriser une revue autonome. Pas de garantie fondée uniquement sur un prompt.

## Ce qui constitue un résultat exploitable

Il manque toujours l’historique réel des 65 jours et le compte exact pour produire un bilan réel. L’extension permet de l’importer, de vérifier les doublons, puis de mesurer les résultats clôturés. Elle ne peut pas reconstruire les pertes latentes, les violations intratrade ou les transactions absentes. Une simulation EOD n’est pas une certification de conformité prop firm.

Avant une stratégie exécutable : spécifier marché, horaires et setup ; obtenir des données horodatées cohérentes ; modéliser commissions, ticks, slippage et taille des contrats ; séparer entraînement/validation/test chronologiquement ; comparer à des baselines ; tester les périodes défavorables ; puis observer en simulation prospective et réconcilier chaque événement. Aucun taux de réussite ni rendement cible n’est inventé ici.

Les benchmarks fournis servent à vérifier le passage des données et l’exécution des bibliothèques. Ils ne valident pas le setup de trading, ne constituent pas un backtest futures, et ne justifient pas l’achat d’un challenge.

## Sources techniques examinées

- https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference
- https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
- https://docs.openbb.co/odp/python/extensions/interface/openbb-mcp
- https://docs.railway.com/deployments/healthchecks
- https://docs.railway.com/volumes/reference
- Les 11 URLs et révisions exactes sont dans `repos.lock.json`.

Les licences de chaque dépôt et de ses dépendances doivent être vérifiées avant redistribution commerciale. Certaines métadonnées GitHub du lock sont indéterminées ; elles ne valent pas autorisation. Aucun code amont ni environnement binaire n’est redistribué dans cette archive.
