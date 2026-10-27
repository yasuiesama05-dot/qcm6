# -*- coding: utf-8 -*-
"""
Script à exécuter UNE SEULE FOIS (dans une console Bash PythonAnywhere,
ou en local) après chaque déploiement/changement d'URL, pour dire à
Telegram où envoyer les mises à jour (webhook).

Utilisation (dans la console Bash de PythonAnywhere, à la racine du
projet, dans le venv où python-telegram-bot est installé) :

    export BOT_TOKEN="xxxx"
    export PA_USERNAME="ton_nom_utilisateur_pythonanywhere"
    python3 set_webhook.py

Cela appelle l'API Telegram setWebhook avec l'URL :
    https://<PA_USERNAME>.pythonanywhere.com/webhook/<BOT_TOKEN>

qui correspond exactement à la route définie dans bot.py (WEBHOOK_PATH).
"""

import os
import sys

import httpx

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PA_USERNAME = os.environ.get("PA_USERNAME", "")

if not BOT_TOKEN:
    sys.exit("⚠️  Variable d'environnement BOT_TOKEN manquante.")
if not PA_USERNAME:
    sys.exit("⚠️  Variable d'environnement PA_USERNAME manquante (ton nom d'utilisateur PythonAnywhere).")

webhook_url = f"https://{PA_USERNAME}.pythonanywhere.com/webhook/{BOT_TOKEN}"
api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"

resp = httpx.post(api_url, data={"url": webhook_url}, timeout=30)
print("URL envoyée à Telegram :", webhook_url)
print("Réponse Telegram :", resp.status_code, resp.text)

# Vérification immédiate de l'état du webhook (utile pour déboguer).
info = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo", timeout=30)
print("\nÉtat actuel du webhook (getWebhookInfo) :")
print(info.text)
