# EA SPORTS FC 27 PC — Price Monitor

Robot léger pour surveiller automatiquement **EA SPORTS FC 27 sur PC**, séparément pour les éditions **Standard** et **Ultimate**, avec notifications Discord.

## Ce qu'il surveille

Le bot alerte si :

- le prix passe à **50 € ou moins** ;
- une baisse atteint **5 € ou 10 %** ;
- une nouvelle offre devient le **plus bas prix jamais observé par le bot** ;
- une offre précédemment indisponible revient en stock.

Il mémorise l'ancien prix, le nouveau prix, le plus bas observé, la boutique, le launcher/DRM, l'état de stock et le lien.

## Pourquoi IsThereAnyDeal

Le projet utilise l'API **IsThereAnyDeal (ITAD)** au lieu de scraper directement toutes les boutiques.

Avantages :

- une API structurée ;
- région France et prix en EUR ;
- boutiques PC, prix normal, remise, launcher/DRM et lien ;
- aucune tentative de contournement anti-bot ;
- beaucoup plus stable qu'une collection de scrapers HTML.

Les IDs actuellement configurés sont :

- Standard : `019f8fc2-975c-734e-92e6-7d99a6c5996a`
- Ultimate : `019f8fc2-a75c-7140-bb31-6dcaa7df910e`

Le script peut les redécouvrir avec `--rediscover`.

**Instant Gaming, CDKeys et Eneba :** cette version ne les scrape pas directement. S'ils ne remontent pas dans la source choisie, ils ne seront pas surveillés. C'est volontaire afin de rester fiable et de ne pas contourner leurs protections.

## Zéro crédit OpenAI / Codex

Après installation, le robot utilise seulement :

- GitHub Actions ;
- IsThereAnyDeal ;
- Discord.

Il n'appelle **aucune API OpenAI** et ne consomme donc aucun crédit ChatGPT/Codex pendant ses vérifications.

## Installation

### 1. Créer le repository

Crée un repository GitHub, par exemple :

`fc27-price-monitor`

Puis envoie tous les fichiers de ce dossier en conservant la structure :

```text
fc27-price-monitor/
├── .github/
│   └── workflows/
│       └── monitor.yml
├── data/
│   └── state.json
├── src/
│   ├── logic.py
│   └── monitor.py
├── tests/
│   └── test_logic.py
├── .gitignore
├── config.json
├── README.md
└── requirements.txt
```

### 2. Créer ton webhook Discord

Dans ton serveur Discord :

1. **Paramètres du serveur**
2. **Intégrations**
3. **Webhooks**
4. **Nouveau webhook**
5. choisis ton salon
6. copie l'URL

Ne mets jamais cette URL dans le code.

### 3. Créer ta clé IsThereAnyDeal

Va sur :

`https://isthereanydeal.com/apps/`

Connecte-toi et enregistre une petite application personnelle, par exemple :

- Name : `FC27 Price Monitor`
- Description : `Personal Discord price watcher`

Copie ensuite l'API key.

Documentation :

`https://docs.isthereanydeal.com/`

### 4. Ajouter les deux secrets GitHub

Repository GitHub :

**Settings → Secrets and variables → Actions → New repository secret**

Crée :

`DISCORD_WEBHOOK_URL`

avec l'URL de ton webhook Discord.

Puis :

`ITAD_API_KEY`

avec ta clé IsThereAnyDeal.

Tu n'as pas besoin de créer un token GitHub supplémentaire : le workflow utilise le `GITHUB_TOKEN` fourni automatiquement par GitHub.

## Test Discord

Dans GitHub :

1. onglet **Actions**
2. ouvre **FC 27 price monitor**
3. **Run workflow**
4. `test_notification` → `true`
5. lance

Une notification de test doit arriver sur Discord.

## Première vérification réelle

Relance **Run workflow** avec :

- `test_notification` → `false`
- `rediscover_game_ids` → `false`

Les logs afficheront les offres PC/EUR détectées.

Au premier lancement, le bot enregistre les prix sans t'envoyer toutes les offres. Il t'alerte toutefois immédiatement si une offre est déjà à **50 € ou moins**.

## Fréquence

Par défaut :

```yaml
- cron: "17 */3 * * *"
```

Donc environ une vérification toutes les **3 heures**.

Pour toutes les 2 heures :

```yaml
- cron: "17 */2 * * *"
```

## Historique

`data/state.json` mémorise les prix.

Le workflow ne crée un commit que lorsque l'état change. Si tout est identique, il ne commit rien.

Une offre absente doit manquer pendant **2 vérifications successives** avant d'être considérée comme indisponible. Cela réduit les faux « restock » provoqués par une panne temporaire de source.

## Modifier les seuils

Dans `config.json` :

```json
"budget_eur": 50.0,
"significant_drop_eur": 5.0,
"significant_drop_pct": 10.0
```

Par exemple, pour être plus sensible :

```json
"significant_drop_eur": 3.0,
"significant_drop_pct": 7.0
```

## Limiter les boutiques

Par défaut :

```json
"shop_allowlist": []
```

Une liste vide = toutes les boutiques PC remontées par ITAD.

Tu peux limiter :

```json
"shop_allowlist": [
  "Steam",
  "EA Store",
  "Epic Game Store",
  "Fanatical",
  "GreenManGaming"
]
```

## Redécouvrir les fiches Standard / Ultimate

Si un jour ITAD réorganise les fiches :

1. GitHub → Actions
2. **Run workflow**
3. `rediscover_game_ids` → `true`

Le robot refuse aussi d'utiliser le même identifiant pour Standard et Ultimate, pour éviter de mélanger les éditions.

## Test local

Aucune dépendance Python externe n'est nécessaire.

Tests :

```bash
python -m unittest discover -s tests -v
```

Test Discord :

```bash
python src/monitor.py --test-discord
```

Il faut avoir défini `DISCORD_WEBHOOK_URL`.

Simulation de la surveillance sans notification et sans modifier l'historique :

```bash
python src/monitor.py --dry-run
```

Il faut alors avoir défini `ITAD_API_KEY`.

## Exemple de notification

```text
🔥 FC 27 Standard passe sous 50 € !

Vendeur : Fanatical
Launcher / DRM : EA App
Ancien prix : 59,99 €
Nouveau prix : 47,99 €
Baisse : -12,00 € (-20,0 %)
✅ Sous 50 €
📉 Plus bas observé : 47,99 €
```

## Sécurité

- ne mets jamais le webhook Discord dans un fichier du repository ;
- ne mets jamais la clé ITAD dans un fichier du repository ;
- utilise uniquement **GitHub Actions Secrets** ;
- si le webhook Discord fuit, supprime-le et recrée-en un ;
- le workflow n'est pas déclenché par les pull requests.

## Sources techniques

- IsThereAnyDeal API : https://docs.isthereanydeal.com/
- IsThereAnyDeal Apps : https://isthereanydeal.com/apps/
- Discord webhooks : https://discord.com/developers/docs/resources/webhook
- GitHub Actions : https://docs.github.com/en/actions
