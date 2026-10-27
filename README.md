# Bot QCM — Médecine (Telegram)

Bot Telegram de QCM (questions à choix multiples) organisés en arbre de
profondeur libre, avec :

- navigation par clavier persistant (module → dossier → … → questions) ;
- réponses correctes cachées sous un **spoiler** ;
- bouton **🚩 Signaler** sous chaque question ;
- génération d'un **examen HTML autonome** (correction en JavaScript) ;
- panneau admin (`/admin`, `/send`, `/reports`) ;
- abonnement obligatoire à une chaîne Telegram.

## Contenu

| Fichier | Rôle |
|---|---|
| `bot.py` | tout le bot (handlers, claviers, examen HTML) |
| `qcm_storage.py` | chargement/sauvegarde des QCM depuis `data/*.json` |
| `render_app.py` | **point d'entrée Render** (serveur webhook aiohttp) |
| `wsgi_pythonanywhere.py` | point d'entrée PythonAnywhere (WSGI Flask) |
| `set_webhook.py` | configuration manuelle du webhook (PythonAnywhere) |
| `render.yaml` | blueprint Render (service + variables) |
| `requirements.txt` | dépendances Python |
| `runtime.txt` | version de Python pour Render |
| `DEPLOY_RENDER.md` | **guide de déploiement Render (recommandé)** |
| `DEPLOY_PYTHONANYWHERE.md` | ancien guide PythonAnywhere |

## Lancer en local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:BOT_TOKEN="123456:ABC..."      # token @BotFather
python bot.py                        # mode polling (pas de webhook)
```

## Variables d'environnement

| Variable | Obligatoire | Défaut |
|---|---|---|
| `BOT_TOKEN` | oui | — |
| `ADMIN_USER_IDS` | non | `5696844039` (séparés par des virgules) |
| `REQUIRED_CHANNEL` | non | `@qcMAX_batna` (vide = désactivé) |
| `CHANNEL_URL` | non | vide |
| `WEBHOOK_SECRET` | non | dérivé du token |
| `PORT` / `RENDER_EXTERNAL_URL` | fournis par Render | — |

## Déploiement

Voir **`DEPLOY_RENDER.md`** (Render, gratuit, mode webhook).
