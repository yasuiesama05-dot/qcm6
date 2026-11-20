# Bot QCM — Médecine (Telegram)

Bot Telegram de QCM (questions à choix multiples) organisés en arbre de
profondeur libre, avec :

- navigation par clavier persistant (module → dossier → … → questions) ;
- réponses correctes cachées sous un **spoiler** ;
- bouton **🚩 Signaler** sous chaque question → message privé à chaque admin,
  avec correction **directement depuis Telegram** (texte, options, bonne
  réponse, suppression) ;
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
| `editor.py` / `editor.bat` | **éditeur local des questions** (navigateur, 127.0.0.1) |
| `push_qcm.py` / `push_qcm.bat` | envoi des `data/*.json` modifiés vers GitHub |
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
| `ADMIN_USER_IDS` | non | `5696844039,5435592397` (séparés par des virgules) |
| `REQUIRED_CHANNEL` | non | `@qcMAX_batna` (vide = désactivé) |
| `CHANNEL_URL` | non | vide |
| `GITHUB_TOKEN` | non | vide (corrections perdues au redéploiement) |
| `GITHUB_REPO` | non | `yasuiesama05-dot/qcm6` |
| `GITHUB_BRANCH` | non | `main` |
| `WEBHOOK_SECRET` | non | dérivé du token |
| `PORT` / `RENDER_EXTERNAL_URL` | fournis par Render | — |

## Déploiement

Voir **`DEPLOY_RENDER.md`** (Render, gratuit, mode webhook).

## Modifier les questions depuis le PC

Deux façons, au choix :

1. **Éditeur avec interface** (recommandé) : double-clique sur `editor.bat`
   (ou `python editor.py`), puis ouvre http://127.0.0.1:8000.
   - choisir le module → leçon → question ;
   - modifier / ajouter / supprimer, avec validation immédiate
     (2 à 5 options, au moins une bonne réponse) ;
   - bouton **🚀 رفع إلى GitHub** pour publier (Render redéploie tout seul) ;
   - sauvegarde automatique des fichiers dans `backups/` avant chaque écriture ;
   - **synchronisation GitHub** : au démarrage, l'éditeur récupère en arrière-plan
     la dernière version de `main` (donc les corrections faites depuis Telegram) —
     l'interface s'ouvre tout de suite, même si le réseau est lent ou coupé ;
     bouton **⬇️ تحديث** pour la refaire à tout moment. Les modifications locales
     non publiées sont mises de côté (`git stash`) puis réappliquées, jamais
     perdues. Option `--no-sync` pour démarrer sans synchroniser.
2. **Édition directe du JSON** : modifier `data/*.json`, puis double-cliquer sur
   `push_qcm.bat` (validation + commit + push).

Les deux outils font un `git pull --rebase` avant l'envoi, pour conserver les
corrections faites depuis Telegram (boutons du message de signalement).

**Savoir si c'est vraiment publié** — la barre d'état de l'éditeur ne dit
jamais « ✔ محدّث » à tort :

| Affichage | Signification |
|---|---|
| ⬆️ *N* تعديل غير مرفوع | *N* commits locaux **pas encore sur GitHub** → appuyer sur 🚀 رفع |
| ● *N* ملف معدّل غير مرفوع | fichiers modifiés pas encore commités |
| ⬇️ *N* تحديث على GitHub | corrections faites depuis Telegram → ⬇️ تحديث |
| ⚠️ تعذّر التحقق من GitHub | réseau coupé, état inconnu (réessayer) |

En cas d'échec du bouton 🚀 رفع, le message distingue trois cas :

- **réseau** (HTTP 502) : rien n'est perdu, le commit local reste en attente →
  réappuyer sur 🚀 رفع plus tard ;
- **permissions** (HTTP 403) : GitHub a refusé le jeton (souvent parce que
  Windows a proposé un autre compte) → voir la section suivante ;
- **conflit** (HTTP 409) : le bot a modifié la même zone ; l'éditeur annule son
  rebase et conserve le commit local → faire ⬇️ تحديث puis refaire la
  modification.

Le dépôt distant doit toujours revenir à `0 0` pour
`git rev-list --left-right --count HEAD...origin/main`.

## Authentification GitHub (éditeur local)

Le dossier envoie vers `yasuiesama05-dot/qcm6` via le remote `origin`. Si Git
utilise un compte Windows différent de celui du dépôt, l'envoi échoue avec :

```
remote: Permission to yasuiesama05-dot/qcm6.git denied to <autre-compte>.
fatal: ... The requested URL returned error: 403
```

Deux solutions :

```bat
:: 1) se connecter une fois avec le bon compte (fenêtre Git Credential Manager)
git push origin main

:: 2) ou placer un jeton personnel dans l'URL du remote
git remote set-url origin https://<TOKEN>@github.com/yasuiesama05-dot/qcm6.git
git push origin main
```

Vérifier ensuite que `git rev-list --left-right --count HEAD...origin/main`
affiche `0 0`. Le jeton reste uniquement dans `.git/config` (jamais commité).
