# -*- coding: utf-8 -*-
"""
Point d'entrée du bot pour Render (et tout hébergeur qui fournit un PORT).

Le bot tourne en mode WEBHOOK : Render lui donne un nom de domaine public
(https://<service>.onrender.com) et Telegram envoie les messages dessus par
de simples requêtes HTTPS. C'est obligatoire sur le plan gratuit de Render,
qui ne propose que des « Web Services » (pas de worker qui tourne en fond).

Variables d'environnement utilisées :
    BOT_TOKEN            (obligatoire) token donné par @BotFather
    RENDER_EXTERNAL_URL  (fourni automatiquement par Render)
    PORT                 (fourni automatiquement par Render)
    WEBHOOK_SECRET       (optionnel) chaîne aléatoire servant de secret dans
                         l'URL du webhook ; générée automatiquement si absente
    ADMIN_USER_IDS       (optionnel) IDs admin séparés par des virgules
    REQUIRED_CHANNEL     (optionnel) chaîne obligatoire, ex. "@ma_chaine"

Lancement local (test) :
    set BOT_TOKEN=xxxx
    python render_app.py
puis ouvrir http://localhost:10000/ pour vérifier que le serveur répond.
"""

import asyncio
import hashlib
import logging
import os
import re
import signal

from aiohttp import web
from telegram import Update

from bot import build_application

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("render_app")


def _webhook_secret(token: str) -> str:
    """Secret utilisé à la fois dans le chemin de l'URL et dans l'en-tête
    X-Telegram-Bot-Api-Secret-Token. Telegram n'accepte que [A-Za-z0-9_-]."""
    raw = (os.environ.get("WEBHOOK_SECRET") or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", raw)
    if cleaned:
        return cleaned[:256]
    # Pas de secret fourni : on en dérive un du token (stable entre les
    # redémarrages, donc pas besoin de reconfigurer quoi que ce soit).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:40]


def _public_base_url() -> str:
    """URL publique du service, fournie par Render."""
    url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip()
    if not url:
        host = (os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "").strip()
        if host:
            url = f"https://{host}"
    return url.rstrip("/")


async def main() -> None:
    token = (os.environ.get("BOT_TOKEN") or "").strip()
    if not token:
        raise SystemExit(
            "⚠️  BOT_TOKEN manquant : ajoute-le dans les variables "
            "d'environnement Render (Dashboard → Environment)."
        )

    port = int(os.environ.get("PORT") or "10000")
    secret = _webhook_secret(token)
    url_path = f"webhook/{secret}"
    base_url = _public_base_url()

    tg_app = build_application()
    await tg_app.initialize()
    await tg_app.start()          # démarre aussi le job_queue (nettoyage périodique)

    async def telegram_webhook(request: web.Request) -> web.Response:
        """Reçoit un update Telegram et le pousse dans la file de PTB."""
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            return web.Response(status=403, text="Forbidden")
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="Bad Request")
        update = Update.de_json(data, tg_app.bot)
        if update is not None:
            await tg_app.update_queue.put(update)
        return web.Response(text="OK")

    async def health(request: web.Request) -> web.Response:
        """Sert aussi de endpoint « keep-alive » pour éviter que le service
        gratuit de Render ne s'endorme après 15 min d'inactivité."""
        return web.Response(text="✅ Le bot QCM est en ligne (mode webhook).")

    server = web.Application()
    server.router.add_post(f"/{url_path}", telegram_webhook)
    server.router.add_get("/", health)
    server.router.add_get("/healthz", health)

    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Serveur HTTP à l'écoute sur le port %s", port)

    if base_url:
        webhook_url = f"{base_url}/{url_path}"
        # On garde les updates en attente (drop_pending_updates=False) : quand
        # l'instance gratuite se réveille après une mise en veille, les
        # messages reçus entre-temps sont traités au lieu d'être perdus.
        for attempt in range(1, 6):
            try:
                await tg_app.bot.set_webhook(
                    url=webhook_url,
                    secret_token=secret,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=False,
                )
                logger.info("Webhook Telegram configuré : %s", webhook_url)
                break
            except Exception as e:
                logger.warning("set_webhook a échoué (essai %s/5) : %s", attempt, e)
                if attempt == 5:
                    raise
                await asyncio.sleep(5)
    else:
        logger.warning(
            "RENDER_EXTERNAL_URL absent : le webhook Telegram n'a pas été "
            "configuré automatiquement. Ouvre %s/ puis configure-le toi-même.",
            f"http://localhost:{port}",
        )

    # Arrêt propre quand Render redéploie (SIGTERM).
    stop_event = asyncio.Event()

    def _request_stop(*_args):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):   # Windows
            signal.signal(sig, _request_stop)

    await stop_event.wait()
    logger.info("Arrêt demandé : fermeture du serveur…")
    await runner.cleanup()
    await tg_app.stop()
    await tg_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
