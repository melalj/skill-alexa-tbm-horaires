# TBM Horaires - Alexa Skill

Horaires temps réel des transports TBM (Bordeaux Métropole) via l'API **SIRI-Lite**.

**Arrêt par défaut :** Quarante Journaux, Tram C → Les Pyrénées (configurable)

## 🎤 Commandes vocales

```
"Alexa, ouvre horaires bordeaux"
"Prochain passage"
"Quand passe le prochain tram ?"
"Changer d'arrêt"
"Enregistre l'arrêt Gambetta"
```

**One-shot :**
```
"Alexa, demande à horaires bordeaux prochain passage"
```

## 🚀 Installation

### Import initial

1. [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask)
2. **Create Skill** → **Import skill**
3. URL : `https://github.com/melalj/skill-alexa-tbm-horaires.git`
4. **Import**

### Déploiement des mises à jour (ASK CLI)

L'import Git ne synchronise qu'une fois. Pour les mises à jour, utilisez ASK CLI :

```bash
# Installation
npm install -g ask-cli

# Configuration (une seule fois)
ask configure

# Déploiement
cd skill-alexa-tbm-horaires
ask deploy
```

## 📁 Structure

```
├── skill-package/
│   ├── skill.json              # Manifest
│   └── interactionModels/
│       └── custom/
│           └── fr-FR.json      # Modèle d'interaction
├── lambda/
│   ├── lambda_function.py      # Handler Alexa
│   ├── api.py                  # Client API TBM
│   └── requirements.txt        # Dépendances
└── ask-resources.json          # Config ASK CLI
```

## ✨ Fonctionnalités

- **Arrêt par défaut** : Fonctionne immédiatement sans configuration
- **Prochains passages** : "Prochain passage", "Quand passe le tram ?"
- **Configuration** : "Enregistre l'arrêt [nom]" → "Tram C" → "Direction Les Pyrénées"
- **Fuzzy matching** : "40 Journaux" = "Quarante Journaux", "Pyrénées" match toutes les directions
- **Persistance** : Arrêt favori sauvegardé (DynamoDB)

## 🗄️ DynamoDB (persistance)

Table créée automatiquement ou manuellement :

```bash
aws dynamodb create-table \
  --table-name tbm-horaires-users \
  --attribute-definitions AttributeName=id,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region eu-west-1
```

Variables Lambda :
- `DYNAMODB_REGION` : `eu-west-1`
- `DYNAMODB_TABLE` : `tbm-horaires-users`

## 🚋 Lignes supportées

| Type | Lignes |
|------|--------|
| Trams | A, B, C, D |
| Lianes | 1-16 |
| Bus | Toutes lignes TBM |
| Batcub | Navettes fluviales |

## 💡 Astuce : Routine Alexa

Pour dire directement "Alexa, prochain tram" sans le nom de la skill :

1. App Alexa → **Routines** → **+**
2. **Quand** : "prochain tram"
3. **Action** : Custom → "demande à horaires bordeaux prochain passage"

## 📡 Source des données

- **API SIRI-Lite** Bordeaux Métropole (clé publique)
- Temps réel tram, bus, Batcub
- Aucune donnée personnelle collectée

## 🙏 Remerciements

Inspiré par [kpagnat/tbm_horaires](https://github.com/kpagnat/tbm_horaires), l'intégration Home Assistant originale.

## 📝 License

MIT
