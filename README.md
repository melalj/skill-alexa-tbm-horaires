# TBM Horaires - Alexa Skill

Horaires temps réel des transports TBM (Bordeaux Métropole) via l'API **SIRI-Lite**.

## 🎤 Commandes vocales

- "Alexa, ouvre TBM Horaires"
- "Prochain passage"
- "Quand passe le prochain tram ?"
- "Enregistre l'arrêt Gambetta pour le tram B"

## 🚀 Installation

1. Allez sur [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask)
2. Cliquez sur **Create Skill** → **Import skill**
3. Entrez l'URL du repo : `https://github.com/melalj/skill-alexa-tbm-horaires.git`
4. Cliquez sur **Import**

## 📁 Structure

```
├── skill-package/
│   ├── skill.json              # Manifest
│   └── interactionModels/
│       └── custom/
│           └── fr-FR.json      # Modèle d'interaction français
└── lambda/
    ├── lambda_function.py      # Handler principal
    ├── api.py                  # Client API TBM
    └── requirements.txt        # Dépendances Python
```

## ✨ Fonctionnalités

- **Demander les prochains passages** : "Prochain passage", "Quand passe le prochain tram ?"
- **Configurer un arrêt favori** : "Enregistre l'arrêt Quinconces pour le tram C direction Gare"
- **Recherche dynamique** : "Prochain bus à Gambetta"
- **Persistance** : Votre arrêt favori est sauvegardé entre les sessions (DynamoDB)

## 🗄️ Configuration DynamoDB (optionnel)

Pour la persistance entre sessions, créez une table DynamoDB :

- Nom : `tbm-horaires-users`
- Partition key : `id` (String)

Variables d'environnement Lambda :

- `DYNAMODB_REGION` : `eu-west-1`
- `DYNAMODB_TABLE` : `tbm-horaires-users`

## 🚋 Lignes supportées

- **Trams** : A, B, C, D
- **Lianes** (bus haute fréquence) : 1-16
- **Bus** : Toutes les lignes TBM
- **Batcub** : Navettes fluviales

## 📡 Source des données

- **API SIRI-Lite** de Bordeaux Métropole (clé publique)
- Temps réel des passages tram, bus, Batcub
- Aucune donnée personnelle collectée

## 🙏 Remerciements

Inspiré par [kpagnat/tbm_horaires](https://github.com/kpagnat/tbm_horaires), l'intégration Home Assistant originale pour les horaires TBM.

## 📝 License

MIT
