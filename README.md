# Hermes Trading — Prop Connect 0.2

Extension additive pour **l’installation Hermes existante dans `Hermes-trading`**, avec journal, simulateur de compte, file de recherche persistante et passerelle MCP authentifiée. Aucun ordre broker. Aucune rentabilité démontrée. Cette archive ne contient ni l’historique privé ni la configuration de votre Railway, qui n’ont pas été accessibles.

## Ce qui est construit

- Interface française : historique CSV/JSONL, PnL net, replay EOD, notes, budget théorique, connexions aux 11 dépôts et résultats des tâches.
- SQLite : import atomique, doublons/conflits, tâches idempotentes, interruption explicite après redémarrage.
- API HTTP + 7 outils MCP : rapports, intégrations, catalogue, skills, soumission et consultation des recherches.
- Adaptateurs : Hermes existant, OpenBB et Vibe via MCP ; QuantStats, vectorbt, PyBroker, TradingAgents et Nautilus dans des interpréteurs isolés.
- Sources amont épinglées dans `repos.lock.json`, récupérées sans exécution par `scripts/bootstrap_repos.py`.
- Dockerfile et configuration Railway pour **un nouveau service d’extension**, dans le même projet que Hermes. Le service et le volume existants sont conservés.

## Démarrage local

Python 3.12, Git et uv requis. Depuis ce dossier :

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-service.txt
python scripts/bootstrap_repos.py --destination vendor
python scripts/bootstrap_envs.py --destination "$PWD/environments"
```

Définir `PROP_ADMIN_TOKEN` avec un secret aléatoire d’au moins 32 caractères, dans un gestionnaire de secrets ou les variables du processus. Ne pas le committer. Puis :

```bash
export PROP_ENVS_DIR="$PWD/environments"
export PROP_DATA_DIR="$PWD/data"
export PROP_BIND=127.0.0.1
.venv/bin/python scripts/start_services.py
```

Ouvrir http://127.0.0.1:8765 et saisir le secret. Le superviseur démarre OpenBB et Vibe sur loopback uniquement s’ils ont été installés et qu’aucun endpoint distant n’est fourni. Le démarrage peut prendre du temps ; sonder après démarrage. Les sources documentaires nécessitent le bootstrap des dépôts.

## Installation dans le dossier existant

```bash
python scripts/install_into_existing.py --target /chemin/Hermes-trading
python scripts/install_into_existing.py --target /chemin/Hermes-trading --apply
```

Le premier appel affiche le plan. Le second ajoute exclusivement `extensions/prop-connect-v0.2`. Aucun fichier existant, historique, Dockerfile ou point d’entrée Hermes n’est remplacé. La présence d’une extension de même nom bloque l’installation. Ce mécanisme ne déploie pas Railway.

Lire [docs/RAILWAY.md](docs/RAILWAY.md) avant déploiement et [docs/ETUDE_INTEGRATION.md](docs/ETUDE_INTEGRATION.md) pour les rôles, limites et preuves. Les exemples sont fictifs. Les répertoires vendor/environments ne sont pas inclus dans l’archive et sont reconstruits par les scripts.

## Vérifications

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s tests_connect -v
```

Les dépendances sont figées depuis les installations locales vérifiées ; les révisions des sources et les versions PyPI sont deux identités distinctes. Les scripts de bootstrap installent du code tiers : examiner ses licences, son coût et ses permissions avant usage commercial. Le build Docker/Railway n’a pas été exécuté ici. Le fichier `docs/VERIFICATION.json` conserve les résultats locaux et leurs limites.
