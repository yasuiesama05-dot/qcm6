# -*- coding: utf-8 -*-
"""
Bot Telegram pour des QCM organisés en arbre de profondeur libre sous chaque
module (ex: module -> leçon -> questions, ou module -> chapitre -> sous-thème
-> année -> questions). Supporte les réponses correctes multiples. La
navigation se fait via un clavier persistant (Reply Keyboard) qui parcourt
l'arbre dynamiquement : un niveau de menu par dossier traversé, jusqu'à
tomber sur une liste de questions.

Toutes les questions d'une feuille sont envoyées d'un coup sous forme de
messages texte normaux (question + options A/B/C...), avec la ou les
bonnes réponses affichées directement sous forme de spoiler (<tg-spoiler>)
à la fin du message (plus de sondages/polls : l'utilisateur lit la
question puis appuie sur le spoiler pour révéler la réponse, à son
rythme). Un bouton « ⬅️ Retour » reste affiché pendant toute la lecture
pour revenir au niveau précédent (ex : choisir une autre année) sans
repasser par le menu principal.

Chaque question est accompagnée d'un bouton inline « 🚩 Signaler »
permettant à l'utilisateur de signaler une question à revoir.

Les questions sont chargées depuis le dossier data/ (un fichier JSON par
module, via qcm_storage.py) — voir qcm_storage.py pour le détail
(load_qcm_data, get_node).

=== EXAMEN HTML AUTONOME ===
Depuis la liste racine des modules, un bouton « 📝 Examen HTML » permet de :
  1. Choisir un module.
  2. Indiquer un nombre de questions (limité au nombre de questions valides
     disponibles dans ce module, à N'IMPORTE QUEL niveau de son arbre).
  3. Recevoir un fichier .html autonome (aucun serveur requis) contenant ce
     nombre de questions tirées AU HASARD parmi tout le module (options de
     chaque question également mélangées), avec correction en JavaScript
     directement dans le navigateur et une note finale calculée sur 20.

=== MODE DE CORRECTION (tout ou rien / partiel) ===
L'examen HTML autonome ne demande plus à l'utilisateur de choisir un mode
de correction : les deux notes (tout ou rien ET partielle) sont calculées
en même temps et affichées ensemble à la fin de l'examen.

=== SIGNALEMENT D'UNE QUESTION (avec type) ===
Sous chaque question envoyée, un bouton « 🚩 Signaler » affiche 3 types de
signalement :
  - ❌ Erreur dans les bonnes réponses
  - 📚 Question dans la mauvaise leçon
  - ⚠️ Question/options incomplètes
Chaque signalement est ajouté à reported_questions.json (type, question,
options, bonne(s) réponse(s), module/leçon, utilisateur, date). La commande
/reports renvoie ce fichier (réservée aux ADMIN_USER_IDS). Aucune
notification n'est envoyée à l'admin : il consulte simplement le fichier.

=== PANNEAU ADMIN (/admin) ===
  - Liste paginée des utilisateurs du bot (bot_users.json), recherche par
    nom / @username / ID, ou saisie directe d'un ID.
  - L'admin choisit un utilisateur puis envoie n'importe quel message
    (texte, photo, fichier, vocal...) qui lui est transmis avec un bouton
    « 💬 Répondre ».
  - Quand l'utilisateur répond, le message arrive à l'admin avec un bouton
    « ✉️ Répondre » pour continuer la conversation.
  - /send <chat_id> <message> reste disponible comme raccourci.

=== ABONNEMENT OBLIGATOIRE À LA CHAÎNE ===
Tant que l'utilisateur n'a pas rejoint REQUIRED_CHANNEL, le bot ne répond
qu'avec un message en arabe l'invitant à suivre la chaîne (boutons « 📢
الانضمام إلى القناة » et « ✅ تحققت من الانضمام »). Les admins sont exemptés.
Le bot doit être administrateur de la chaîne pour pouvoir vérifier les membres.

=== ROBUSTESSE / CHARGE (300+ utilisateurs) ===
1. concurrent_updates=True : les updates de différents utilisateurs sont
   traités en parallèle au lieu d'un traitement strictement séquentiel.
2. Rate limiter global + retry automatique sur RetryAfter/TimedOut pour
   tous les envois de messages.
3. Nettoyage périodique de question_context (TTL) pour éviter une fuite mémoire.
4. Cache de QCM_DATA avec throttle de rechargement (pas de lecture disque
   à chaque navigation de menu).

Installation :
    pip install "python-telegram-bot[job-queue]" --upgrade

Lancement :
    python bot.py
"""

import asyncio
import html
import io
import json
import logging
import os
import random
import re
import time
import uuid

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    TypeHandler,
    ApplicationHandlerStop,
    filters,
)

from qcm_storage import load_qcm_data, get_node

# ============ CONFIGURATION ============
# Le secret du bot n'est JAMAIS écrit ici en clair : il est lu depuis la
# variable d'environnement BOT_TOKEN. Sur PythonAnywhere, définis-la tout en
# haut de ton fichier WSGI (voir DEPLOY.md), par exemple :
#   os.environ["BOT_TOKEN"] = "xxxx"
# En local, tu peux la définir dans ton terminal avant de lancer le script :
#   export BOT_TOKEN="xxxx"        (Linux/Mac)
#   $env:BOT_TOKEN="xxxx"          (Windows PowerShell)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

SEND_DELAY_SECONDS = 0.35       # petit délai entre l'envoi de chaque question pour éviter les limites Telegram

TG_MESSAGE_MAX = 3900   # marge de sécurité sous la limite Telegram de 4096 caractères par message

BTN_BACK = "⬅️ Retour"
BTN_HOME = "🏠 Retour à l'accueil"
BTN_EXAM = "📝 Examen HTML"
LETTERS = ["A", "B", "C", "D", "E"]

EXAM_TOTAL_POINTS = 20   # note finale de l'examen HTML généré

# ============ SIGNALEMENT DE QUESTION (l'utilisateur signale une question à revoir) ============
REPORT_CALLBACK_PREFIX = "report:"
REPORT_TYPE_PREFIX = "rtype:"
REPORT_CANCEL_PREFIX = "rcancel:"
REPORT_BUTTON_LABEL = "🚩 Signaler"
REPORT_BUTTON_LABEL_DONE = "✅ Signalé"
REPORT_TYPES = {
    "answers": "❌ Erreur dans les bonnes réponses",
    "lesson": "📚 Question dans la mauvaise leçon",
    "incomplete": "⚠️ Question/options incomplètes",
}
REPORTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reported_questions.json")

# Identifiants Telegram numériques des administrateurs : accès à /admin,
# /send et /reports. Si le set est vide, /admin et /send sont désactivés et
# /reports reste ouvert à tout le monde.
# Peut être surchargé par la variable d'environnement ADMIN_USER_IDS
# (IDs séparés par des virgules, ex. "123456789,987654321").
DEFAULT_ADMIN_USER_IDS = "5696844039"


def _parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return ids


ADMIN_USER_IDS: set[int] = _parse_admin_ids(
    os.environ.get("ADMIN_USER_IDS", DEFAULT_ADMIN_USER_IDS)
)

# ============ ABONNEMENT OBLIGATOIRE À LA CHAÎNE ============
# Le bot ne fonctionne que si l'utilisateur a rejoint cette chaîne.
#  - Chaîne publique : mets son @username, ex. "@ma_chaine"
#  - Chaîne privée   : mets son ID numérique (ex. -1001234567890) ET renseigne
#                      CHANNEL_URL avec le lien d'invitation.
#  - Laisse REQUIRED_CHANNEL vide ("") pour désactiver cette vérification.
# IMPORTANT : le bot doit être ADMINISTRATEUR de la chaîne, sinon Telegram
# l'empêche de vérifier les abonnés.
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "@qcMAX_batna")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "")   # optionnel : lien d'invitation / lien de la chaîne
SUB_CHECK_CB = "chk:sub"
SUB_CACHE_TTL_SECONDS = 600      # un abonné vérifié n'est pas re-vérifié pendant 10 min
JOIN_MESSAGE = (
    "👋 مرحبًا!\n\n"
    "يرجى متابعة قناة البوت لتصلك آخر التحديثات والأسئلة الجديدة، "
    "ثم يمكنك استخدام البوت.\n\n"
    "بعد الانضمام إلى القناة اضغط على «✅ تحققت من الانضمام»."
)

# ============ CONFIGURATION CHARGE / ROBUSTESSE ============
GLOBAL_RATE_LIMIT_PER_SEC = 25      # marge sous la limite Telegram globale (~30 msg/s)
MAX_SEND_RETRIES = 5                # nombre max de tentatives sur erreur transitoire
QUESTION_CONTEXT_TTL_SECONDS = 3 * 3600   # durée de vie max d'un contexte avant nettoyage
QUESTION_CONTEXT_CLEANUP_INTERVAL = 1800  # fréquence du nettoyage périodique
QCM_RELOAD_THROTTLE_SECONDS = 10    # ne recharge le JSON que si + de N secondes depuis le dernier chargement

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

QCM_DATA = load_qcm_data()
_last_qcm_reload_ts = time.monotonic()

# Associe un token unique aux infos nécessaires pour permettre à
# l'utilisateur de signaler une question (via le bouton « 🚩 Signaler »).
# Chaque entrée porte "created_at" pour permettre un nettoyage TTL.
question_context: dict[str, dict] = {}

# Verrou pour éviter que deux signalements simultanés ne s'écrasent en
# écrivant en même temps dans le fichier reported_questions.json.
_reports_lock = asyncio.Lock()


# ============ LIMITEUR DE DÉBIT GLOBAL (anti flood-control Telegram) ============
class GlobalRateLimiter:
    """Limite le nombre d'appels API Telegram par seconde sur TOUT le bot
    (et pas seulement par utilisateur), pour éviter les erreurs 429 quand
    beaucoup d'utilisateurs envoient/reçoivent des messages en même temps."""

    def __init__(self, max_per_second: float):
        self._min_interval = 1.0 / max_per_second
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            wait_time = self._last_call + self._min_interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_call = time.monotonic()


rate_limiter = GlobalRateLimiter(GLOBAL_RATE_LIMIT_PER_SEC)


async def call_with_retry(coro_factory, *, max_retries: int = MAX_SEND_RETRIES, label: str = "appel Telegram"):
    """Exécute un appel API Telegram (send_message, ...) en respectant
    le rate limiter global et en réessayant automatiquement en cas d'erreur
    transitoire (flood control, timeout réseau), au lieu d'abandonner l'action.

    coro_factory doit être une fonction sans argument qui RETOURNE une coroutine
    (pas la coroutine elle-même), pour pouvoir la relancer proprement à chaque essai.
    """
    attempt = 0
    while True:
        attempt += 1
        await rate_limiter.wait()
        try:
            return await coro_factory()
        except RetryAfter as e:
            wait_s = float(getattr(e, "retry_after", 1)) + 0.5
            logger.warning("Flood control Telegram sur %s, attente %.1fs (essai %s)", label, wait_s, attempt)
            await asyncio.sleep(wait_s)
        except (TimedOut, NetworkError) as e:
            if attempt >= max_retries:
                logger.error("Échec définitif de %s après %s essais : %s", label, attempt, e)
                raise
            backoff = min(2 ** attempt, 20)
            logger.warning("Erreur réseau sur %s (essai %s), retry dans %ss : %s", label, attempt, backoff, e)
            await asyncio.sleep(backoff)


def is_valid_question(q: dict) -> bool:
    """Validité structurelle de base (indépendante des limites de longueur Telegram)."""
    if not isinstance(q, dict):
        return False
    if not q.get("question") or not q.get("options") or not all(q["options"]):
        return False
    if not (2 <= len(q["options"]) <= 5):
        return False
    if not q.get("correct_option_ids"):
        return False
    return True


def build_question_message(idx: int, total: int, q: dict) -> str:
    """Construit le texte d'une question (question + options A/B/C... + bonne(s)
    réponse(s) cachée(s) sous spoiler + explication éventuelle).

    Le spoiler liste TOUJOURS toutes les lettres de la question avec un
    repère ✅/❌ pour chacune, plutôt que de ne lister que les bonnes
    réponses : sa largeur ne dépend donc que du nombre d'options (déjà
    visible dans la question elle-même), jamais du nombre de bonnes
    réponses. Impossible de deviner combien de réponses sont correctes
    juste en regardant la taille du spoiler flouté avant de l'ouvrir."""
    options_lines = "\n".join(
        f"{LETTERS[i]}. {html.escape(opt)}" for i, opt in enumerate(q["options"])
    )
    correct_ids = set(q["correct_option_ids"])
    marks = " ".join(
        f"{LETTERS[i]}{'✅' if i in correct_ids else '❌'}"
        for i in range(len(q["options"]))
    )
    text = (
        f"[{idx + 1}/{total}] {html.escape(q['question'])}\n\n"
        f"{options_lines}\n\n"
        f"Réponse : <tg-spoiler>{marks}</tg-spoiler>"
    )
    if q.get("explanation"):
        text += f"\n💡 {html.escape(q['explanation'])}"
    return text


# ============ EXAMEN HTML AUTONOME (génération à la demande) ============

EXAM_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr" dir="ltr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Examen - __MODULE__</title>
<style>
  :root {
    --bg: #f4f6fb;
    --card: #ffffff;
    --primary: #2f6f4f;
    --primary-dark: #234f38;
    --danger: #c0392b;
    --text: #1f2933;
    --muted: #6b7280;
    --border: #e2e8f0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: "Segoe UI", Tahoma, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px 16px 80px;
  }
  .header {
    max-width: 760px;
    margin: 0 auto 24px;
    text-align: center;
  }
  .header h1 {
    margin: 0 0 6px;
    color: var(--primary-dark);
    font-size: 24px;
  }
  .header p {
    margin: 0;
    color: var(--muted);
    font-size: 14px;
  }
  .quiz {
    max-width: 760px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .question-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .question-card h3 {
    margin: 0 0 12px;
    font-size: 16px;
    line-height: 1.5;
  }
  .badge {
    display: inline-block;
    background: var(--primary);
    color: #fff;
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 999px;
    margin-inline-end: 8px;
  }
  .option {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 10px;
    border-radius: 8px;
    margin-bottom: 6px;
    cursor: pointer;
    border: 1px solid transparent;
  }
  .option:hover { background: #f1f5f9; }
  .option input { transform: scale(1.15); accent-color: var(--primary); }
  .option.correct { background: #e6f6ea; border-color: #34a853; }
  .option.wrong { background: #fdeaea; border-color: var(--danger); }
  .option.missing { border-color: #3b82f6; border-style: dashed; }
  .status { margin-top: 10px; font-size: 13px; font-weight: 600; }
  .status.ok { color: #1b7f3a; }
  .status.ko { color: var(--danger); }
  .status.partial { color: #b8860b; }
  .footer-bar {
    max-width: 760px;
    margin: 24px auto 0;
    text-align: center;
  }
  #submitBtn {
    background: var(--primary);
    color: #fff;
    border: none;
    padding: 12px 28px;
    font-size: 15px;
    border-radius: 999px;
    cursor: pointer;
  }
  #submitBtn:hover { background: var(--primary-dark); }
  #submitBtn:disabled { opacity: 0.5; cursor: default; }
  #result {
    max-width: 760px;
    margin: 20px auto 0;
    text-align: center;
    font-size: 18px;
    font-weight: 700;
    color: var(--primary-dark);
    line-height: 1.6;
  }
</style>
</head>
<body>
  <div class="header">
    <h1>📝 Examen : __MODULE__</h1>
    <p>Nombre de questions : __N__ — Note finale sur __TOTAL__</p>
  </div>

  <div class="quiz" id="quiz"></div>

  <div class="footer-bar">
    <button id="submitBtn" onclick="gradeExam()">Corriger l'examen</button>
  </div>
  <div id="result"></div>

<script>
const QUESTIONS = __DATA__;
const TOTAL_POINTS = __TOTAL__;

function renderQuiz() {
  const container = document.getElementById('quiz');
  QUESTIONS.forEach((q, qi) => {
    const card = document.createElement('div');
    card.className = 'question-card';
    card.id = 'card-' + qi;

    const title = document.createElement('h3');
    title.innerHTML = '<span class="badge">' + (qi + 1) + '</span>' + escapeHtml(q.question);
    card.appendChild(title);

    q.options.forEach((opt, oi) => {
      const label = document.createElement('label');
      label.className = 'option';
      label.id = 'opt-' + qi + '-' + oi;
      const input = document.createElement('input');
      // Toujours une case à cocher, y compris pour les questions à une
      // seule bonne réponse : ainsi rien dans l'interface ne trahit si la
      // question attend une ou plusieurs réponses.
      input.type = 'checkbox';
      input.name = 'q' + qi;
      input.value = oi;
      label.appendChild(input);
      const span = document.createElement('span');
      span.textContent = opt;
      label.appendChild(span);
      card.appendChild(label);
    });

    const status = document.createElement('div');
    status.className = 'status';
    status.id = 'status-' + qi;
    card.appendChild(status);

    container.appendChild(card);
  });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function gradeExam() {
  const perQuestion = TOTAL_POINTS / QUESTIONS.length;
  let totalStrict = 0;
  let totalPartial = 0;

  QUESTIONS.forEach((q, qi) => {
    const inputs = document.querySelectorAll('input[name="q' + qi + '"]');
    const selected = [];
    inputs.forEach(inp => {
      if (inp.checked) selected.push(parseInt(inp.value, 10));
      inp.disabled = true;
    });
    selected.sort((a, b) => a - b);
    const correct = q.correct.slice().sort((a, b) => a - b);
    const wrongSelected = selected.filter(oi => !correct.includes(oi));
    const overlap = selected.filter(oi => correct.includes(oi));
    const isFullMatch = JSON.stringify(selected) === JSON.stringify(correct);

    // Note "tout ou rien" : point entier seulement si la réponse est
    // parfaitement exacte.
    const fractionStrict = isFullMatch ? 1 : 0;
    totalStrict += fractionStrict * perQuestion;

    // Note "partielle" : crédit proportionnel au nombre de bonnes options
    // cochées, à condition de n'avoir coché aucune mauvaise option.
    let fractionPartial = 0;
    if (wrongSelected.length === 0 && overlap.length > 0) {
      fractionPartial = overlap.length / correct.length;
    }
    totalPartial += fractionPartial * perQuestion;

    q.options.forEach((opt, oi) => {
      const label = document.getElementById('opt-' + qi + '-' + oi);
      if (correct.includes(oi)) {
        label.classList.add('correct');
        if (!selected.includes(oi)) label.classList.add('missing');
      } else if (selected.includes(oi)) {
        label.classList.add('wrong');
      }
    });

    const statusEl = document.getElementById('status-' + qi);
    let state;
    if (isFullMatch) {
      statusEl.textContent = '✅ Correct';
      state = 'ok';
    } else if (fractionPartial > 0) {
      const missingCount = correct.length - overlap.length;
      statusEl.textContent =
        '🟡 Partiellement correct (' + Math.round(fractionPartial * 100) + '%) — il te manque ' +
        missingCount + ' choix correct(s)';
      state = 'partial';
    } else {
      statusEl.textContent = '❌ Incorrect';
      state = 'ko';
    }
    statusEl.classList.add(state);
  });

  document.getElementById('submitBtn').disabled = true;
  document.getElementById('result').innerHTML =
    'Note (tout ou rien) : ' + totalStrict.toFixed(2) + ' / ' + TOTAL_POINTS + '<br>' +
    'Note (partielle) : ' + totalPartial.toFixed(2) + ' / ' + TOTAL_POINTS;
  document.getElementById('result').scrollIntoView({ behavior: 'smooth' });
}

renderQuiz();
</script>
</body>
</html>
"""


def collect_module_questions(module: str) -> list[dict]:
    """Rassemble toutes les questions valides d'un module, en parcourant
    récursivement TOUT son arbre (quelle que soit sa profondeur), pour
    l'examen HTML (les limites de longueur Telegram ne s'y appliquent pas)."""
    result: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for q in node:
                if is_valid_question(q):
                    result.append(q)
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)

    walk(QCM_DATA.get(module, {}))
    return result


def build_exam_html(module: str, questions: list[dict]) -> str:
    """Construit un examen HTML autonome (correction en JavaScript, dans le
    navigateur, aucun serveur requis) à partir d'une liste de questions déjà
    tirées au hasard. Les options de chaque question sont également
    mélangées. Les deux notes finales (tout ou rien et partielle) sont
    calculées ensemble et affichées ensemble à la fin de l'examen, réparties
    également entre toutes les questions."""
    exam_questions = []
    for q in questions:
        order = list(range(len(q["options"])))
        random.shuffle(order)
        shuffled_options = [q["options"][i] for i in order]
        new_correct = sorted(order.index(i) for i in q["correct_option_ids"])
        exam_questions.append({
            "question": q["question"],
            "options": shuffled_options,
            "correct": new_correct,
        })

    out = EXAM_HTML_TEMPLATE
    out = out.replace("__MODULE__", html.escape(module))
    out = out.replace("__N__", str(len(exam_questions)))
    out = out.replace("__TOTAL__", str(EXAM_TOTAL_POINTS))
    out = out.replace("__DATA__", json.dumps(exam_questions, ensure_ascii=False))
    return out


# ============ SUIVI DES QUESTIONS ENVOYÉES (pour signalement) ============

def register_question_context(
    chat_id: int,
    question: dict,
    correct_ids: set,
    module: str | None = None,
    path: list | None = None,
    quiz_label: str | None = None,
) -> str:
    """Enregistre les infos d'une question sous un token court, pour permettre
    à l'utilisateur de signaler cette question si besoin."""
    token = uuid.uuid4().hex[:12]
    question_context[token] = {
        "chat_id": chat_id,
        "question": question["question"],
        "options": question["options"],
        "correct_option_ids": set(correct_ids),
        "message_id": None,         # rempli juste après l'envoi du message de correction
        "base_text": None,          # texte du message de correction
        "module": module,
        "path": list(path) if path else [],
        "quiz_label": quiz_label,
        "reported": False,
        "created_at": time.monotonic(),
    }
    return token


async def save_report(record: dict):
    """Ajoute un signalement au fichier reported_questions.json (liste JSON),
    protégé par un verrou pour éviter les écritures concurrentes conflictuelles."""
    async with _reports_lock:
        data = []
        try:
            if os.path.exists(REPORTS_FILE):
                with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list):
                        data = loaded
        except Exception as e:
            logger.error("Impossible de lire %s, on repart d'une liste vide : %s", REPORTS_FILE, e)
        data.append(record)
        try:
            with open(REPORTS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Impossible d'écrire dans %s : %s", REPORTS_FILE, e)


def cleanup_question_context(context: ContextTypes.DEFAULT_TYPE = None):
    """Supprime les contextes de question trop anciens pour éviter une fuite
    mémoire sur un processus qui tourne longtemps avec beaucoup d'utilisateurs."""
    now = time.monotonic()
    expired = [t for t, info in question_context.items() if now - info.get("created_at", now) > QUESTION_CONTEXT_TTL_SECONDS]
    for t in expired:
        question_context.pop(t, None)
    if expired:
        logger.info("Nettoyage question_context : %s entrées expirées supprimées", len(expired))


# ============ SIGNALEMENT : BOUTONS + TYPES ============

def build_question_button_markup(token: str, show_types: bool = False) -> InlineKeyboardMarkup | None:
    """Clavier inline sous une question : soit le bouton « 🚩 Signaler »,
    soit (show_types=True) les 3 types de signalement + « Annuler »."""
    info = question_context.get(token)
    if info is None:
        return None

    if show_types and not info.get("reported"):
        rows = [
            [InlineKeyboardButton(label, callback_data=f"{REPORT_TYPE_PREFIX}{token}:{key}")]
            for key, label in REPORT_TYPES.items()
        ]
        rows.append([InlineKeyboardButton("↩️ Annuler", callback_data=f"{REPORT_CANCEL_PREFIX}{token}")])
        return InlineKeyboardMarkup(rows)

    report_label = REPORT_BUTTON_LABEL_DONE if info.get("reported") else REPORT_BUTTON_LABEL
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(report_label, callback_data=f"{REPORT_CALLBACK_PREFIX}{token}")]]
    )


async def _edit_markup(query, markup, label):
    try:
        await call_with_retry(
            lambda: query.edit_message_reply_markup(reply_markup=markup), label=label
        )
    except Exception:
        pass


async def handle_report_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clic sur « 🚩 Signaler » : affiche les 3 types de signalement."""
    query = update.callback_query
    token = query.data[len(REPORT_CALLBACK_PREFIX):]
    info = question_context.get(token)

    if info is None:
        await query.answer("Cette question n'est plus disponible pour être signalée.", show_alert=True)
        return
    if info.get("reported"):
        await query.answer("Tu as déjà signalé cette question, merci !")
        return

    await query.answer()
    await _edit_markup(query, build_question_button_markup(token, show_types=True), "edit_markup(report types)")


async def handle_report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clic sur « Annuler » : revient au simple bouton « 🚩 Signaler »."""
    query = update.callback_query
    token = query.data[len(REPORT_CANCEL_PREFIX):]
    await query.answer()
    await _edit_markup(query, build_question_button_markup(token), "edit_markup(report cancel)")


async def handle_report_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clic sur un type de signalement : enregistre le signalement (type,
    question, options, bonne(s) réponse(s), module/leçon) dans
    reported_questions.json. Aucune notification n'est envoyée à l'admin."""
    query = update.callback_query
    try:
        _, token, type_key = query.data.split(":", 2)
    except ValueError:
        await query.answer()
        return

    info = question_context.get(token)
    if info is None or type_key not in REPORT_TYPES:
        await query.answer("Cette question n'est plus disponible pour être signalée.", show_alert=True)
        return
    if info.get("reported"):
        await query.answer("Tu as déjà signalé cette question, merci !")
        return

    info["reported"] = True
    user = update.effective_user

    correct_texts = [info["options"][i] for i in sorted(info["correct_option_ids"])]
    record = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "report_type": type_key,
        "report_type_label": REPORT_TYPES[type_key],
        "chat_id": info["chat_id"],
        "user_id": user.id if user else None,
        "username": (user.username or user.full_name) if user else None,
        "module": info.get("module"),
        "path": info.get("path", []),
        "quiz_label": info.get("quiz_label"),
        "question": info["question"],
        "options": info["options"],
        "correct_options": correct_texts,
    }
    await save_report(record)

    await query.answer("🚩 Merci, la question a été signalée pour relecture.", show_alert=True)
    await _edit_markup(query, build_question_button_markup(token), "edit_markup(report done)")


async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /reports : envoie le fichier des questions signalées (si
    ADMIN_USER_IDS est configuré, réservé à ces utilisateurs)."""
    user_id = update.effective_user.id if update.effective_user else None
    if ADMIN_USER_IDS and user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ Commande réservée.")
        return

    if not os.path.exists(REPORTS_FILE):
        await update.message.reply_text("Aucune question signalée pour le moment.")
        return

    try:
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = len(data) if isinstance(data, list) else 0
    except Exception:
        count = "?"

    with open(REPORTS_FILE, "rb") as f:
        await update.message.reply_document(
            document=InputFile(f, filename=os.path.basename(REPORTS_FILE)),
            caption=f"🚩 {count} question(s) signalée(s).",
        )


# ============ /send (raccourci admin) ============

async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/send <chat_id> <message> — réservé aux admins (ADMIN_USER_IDS)."""
    user_id = update.effective_user.id if update.effective_user else None
    if not ADMIN_USER_IDS or user_id not in ADMIN_USER_IDS:
        return

    # maxsplit=2 pour conserver les retours à la ligne du message
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
        await update.message.reply_text("Usage :\n/send <chat_id> <message>")
        return

    try:
        await call_with_retry(
            lambda: context.bot.send_message(int(parts[1]), parts[2]),
            label="send_message(/send admin)",
        )
        await update.message.reply_text("✅ Envoyé")
    except Exception as e:
        await update.message.reply_text(f"❌ Échec : {e}")


# ============ LISTE DES UTILISATEURS + PANNEAU ADMIN (/admin) ============
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_users.json")
USERS_PER_PAGE = 8
ADM = "adm:"
REPLY_CB = "rep:"
REPLY_CANCEL = "repc"

_users: dict[str, dict] = {}
_users_dirty = False


def _load_users():
    global _users
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                _users = json.load(f)
    except Exception as e:
        logger.error("Lecture de %s impossible : %s", USERS_FILE, e)
        _users = {}


def _save_users():
    global _users_dirty
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(_users, f, ensure_ascii=False, indent=1)
        _users_dirty = False
    except Exception as e:
        logger.error("Écriture de %s impossible : %s", USERS_FILE, e)


_load_users()


async def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enregistre chaque utilisateur (chat privé) qui interagit avec le bot."""
    global _users_dirty
    user, chat = update.effective_user, update.effective_chat
    if not user or user.is_bot or not chat or chat.type != "private":
        return
    now = time.strftime("%Y-%m-%d %H:%M")
    key = str(chat.id)
    rec = _users.get(key)
    if rec is None:
        _users[key] = {
            "id": chat.id, "name": user.full_name, "username": user.username or "",
            "first_seen": now, "last_seen": now,
        }
        _save_users()
    else:
        rec["last_seen"] = now
        rec["name"] = user.full_name
        rec["username"] = user.username or ""
        _users_dirty = True   # écrit périodiquement par le job de nettoyage


def user_label(rec: dict) -> str:
    uname = f" @{rec['username']}" if rec.get("username") else ""
    return f"👤 {rec.get('name') or rec['id']}{uname}"[:60]


def is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(ADMIN_USER_IDS and u and u.id in ADMIN_USER_IDS)


def build_admin_panel(page: int = 0):
    users = sorted(_users.values(), key=lambda r: r.get("last_seen", ""), reverse=True)
    pages = max(1, (len(users) + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = users[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]

    rows = [[InlineKeyboardButton(user_label(r), callback_data=f"{ADM}u:{r['id']}")] for r in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"{ADM}p:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data=f"{ADM}p:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"{ADM}p:{page + 1}"))
    rows.append(nav)
    rows.append([
        InlineKeyboardButton("🔎 Rechercher", callback_data=f"{ADM}s"),
        InlineKeyboardButton("🔢 Par ID", callback_data=f"{ADM}i"),
    ])
    text = f"👥 Utilisateurs du bot : {len(users)}\nChoisis un utilisateur pour lui écrire."
    return text, InlineKeyboardMarkup(rows)


def cancel_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annuler", callback_data=f"{ADM}c")]])


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    context.user_data.pop("admin_state", None)
    text, markup = build_admin_panel(0)
    await update.message.reply_text(text, reply_markup=markup)


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(update):
        await query.answer()
        return
    await query.answer()
    parts = query.data[len(ADM):].split(":")
    action = parts[0]

    async def edit(text, markup):
        try:
            await query.edit_message_text(text, reply_markup=markup)
        except BadRequest:
            pass

    if action == "p":
        context.user_data.pop("admin_state", None)
        text, markup = build_admin_panel(int(parts[1]))
        await edit(text, markup)
    elif action == "c":
        context.user_data.pop("admin_state", None)
        text, markup = build_admin_panel(0)
        await edit(text, markup)
    elif action == "u":
        target = int(parts[1])
        rec = _users.get(str(target), {"id": target, "name": str(target)})
        context.user_data["admin_state"] = {"mode": "compose", "target": target}
        await edit(
            f"✍️ Écris maintenant ton message pour :\n{user_label(rec)}\n🆔 {target}\n\n"
            "(texte, photo, fichier, vocal… tout est accepté)",
            cancel_markup(),
        )
    elif action == "s":
        context.user_data["admin_state"] = {"mode": "search"}
        await edit("🔎 Écris un nom, un @username ou un ID :", cancel_markup())
    elif action == "i":
        context.user_data["admin_state"] = {"mode": "id"}
        await edit("🔢 Écris l'ID (chat id) de l'utilisateur :", cancel_markup())


async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercepte les messages de l'admin quand il est en train de chercher
    ou d'écrire à un utilisateur (groupe -2 : passe avant handle_text)."""
    if not is_admin(update) or not update.message:
        return
    state = context.user_data.get("admin_state")
    if not state:
        return

    msg = update.message
    text = (msg.text or "").strip()

    # Si l'admin appuie sur un bouton du clavier du bot, on annule le mode admin
    if text in (BTN_HOME, BTN_BACK, BTN_EXAM):
        context.user_data.pop("admin_state", None)
        return

    mode = state["mode"]

    if mode in ("search", "id") and not text:
        await msg.reply_text("Envoie du texte, s'il te plaît.", reply_markup=cancel_markup())

    elif mode == "search":
        q = text.lower().lstrip("@")
        found = [
            r for r in _users.values()
            if q in (r.get("name") or "").lower() or q in (r.get("username") or "").lower() or q == str(r["id"])
        ][:10]
        context.user_data.pop("admin_state", None)
        if not found:
            await msg.reply_text("Aucun résultat.", reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("👥 Liste", callback_data=f"{ADM}p:0")]]))
        else:
            rows = [[InlineKeyboardButton(user_label(r), callback_data=f"{ADM}u:{r['id']}")] for r in found]
            rows.append([InlineKeyboardButton("👥 Liste", callback_data=f"{ADM}p:0")])
            await msg.reply_text(f"🔎 {len(found)} résultat(s) :", reply_markup=InlineKeyboardMarkup(rows))

    elif mode == "id":
        if not text.lstrip("-").isdigit():
            await msg.reply_text("ID invalide, réessaie :", reply_markup=cancel_markup())
        else:
            target = int(text)
            context.user_data["admin_state"] = {"mode": "compose", "target": target}
            await msg.reply_text(f"✍️ Écris maintenant ton message pour 🆔 {target} :", reply_markup=cancel_markup())

    elif mode == "compose":
        target = state["target"]
        context.user_data.pop("admin_state", None)
        again = InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Écrire encore", callback_data=f"{ADM}u:{target}")],
            [InlineKeyboardButton("👥 Liste", callback_data=f"{ADM}p:0")],
        ])
        reply_kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("💬 Répondre", callback_data=f"{REPLY_CB}{update.effective_user.id}")]]
        )
        try:
            await call_with_retry(
                lambda: context.bot.copy_message(target, msg.chat_id, msg.message_id, reply_markup=reply_kb),
                label="copy_message(admin)",
            )
            await msg.reply_text("✅ Envoyé", reply_markup=again)
        except Forbidden:
            await msg.reply_text("❌ Cet utilisateur a bloqué le bot.", reply_markup=again)
        except BadRequest as e:
            await msg.reply_text(f"❌ Échec : {e}\n(il doit avoir fait /start au moins une fois)", reply_markup=again)
        except Exception as e:
            await msg.reply_text(f"❌ Échec : {e}", reply_markup=again)

    raise ApplicationHandlerStop   # empêche handle_text de traiter ce message


# ============ RÉPONSE DE L'UTILISATEUR À L'ADMIN ============

async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """L'utilisateur appuie sur « 💬 Répondre » sous le message de l'admin."""
    query = update.callback_query
    context.user_data["reply_to_admin"] = int(query.data[len(REPLY_CB):])
    await query.answer()
    await call_with_retry(
        lambda: context.bot.send_message(
            query.message.chat_id,
            "✍️ Écris ta réponse (texte, photo, vocal…) :",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Annuler", callback_data=REPLY_CANCEL)]]),
        ),
        label="send_message(invite reponse)",
    )


async def handle_reply_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("reply_to_admin", None)
    await update.callback_query.answer("Annulé")
    try:
        await update.callback_query.edit_message_text("❌ Annulé")
    except BadRequest:
        pass


async def user_reply_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transmet à l'admin le prochain message de l'utilisateur après « Répondre »."""
    admin_id = context.user_data.get("reply_to_admin")
    msg = update.message
    if not admin_id or not msg:
        return

    context.user_data.pop("reply_to_admin", None)   # une seule réponse par clic
    if (msg.text or "").strip() in (BTN_HOME, BTN_BACK, BTN_EXAM):
        return  # navigation normale : on annule simplement le mode réponse

    user = update.effective_user
    rec = _users.get(str(msg.chat_id), {
        "id": msg.chat_id, "name": user.full_name, "username": user.username or "",
    })
    header = f"💬 Réponse de {user_label(rec)}\n🆔 {msg.chat_id}"
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✉️ Répondre", callback_data=f"{ADM}u:{msg.chat_id}")]]
    )
    try:
        await call_with_retry(lambda: context.bot.send_message(admin_id, header), label="send_message(reponse header)")
        await call_with_retry(
            lambda: context.bot.copy_message(admin_id, msg.chat_id, msg.message_id, reply_markup=kb),
            label="copy_message(reponse user)",
        )
        await msg.reply_text("✅ Ta réponse a été envoyée.")
    except Exception as e:
        logger.error("Échec de transmission de la réponse à l'admin : %s", e)
        await msg.reply_text("❌ Échec de l'envoi, réessaie plus tard.")
    raise ApplicationHandlerStop


# ============ ABONNEMENT OBLIGATOIRE : VÉRIFICATION ============
_sub_cache: dict[int, float] = {}   # user_id -> instant de la dernière vérification positive


def _channel_url() -> str | None:
    if CHANNEL_URL:
        return CHANNEL_URL
    if isinstance(REQUIRED_CHANNEL, str) and REQUIRED_CHANNEL.startswith("@"):
        return f"https://t.me/{REQUIRED_CHANNEL[1:]}"
    return None


async def is_subscribed(bot, user_id: int) -> bool:
    """Vérifie que l'utilisateur est membre de la chaîne obligatoire. Les
    résultats positifs sont mis en cache pour ne pas appeler l'API à chaque
    message. En cas d'erreur de configuration (bot non admin de la chaîne,
    nom de chaîne invalide...), on laisse passer et on logue l'erreur pour ne
    pas bloquer tous les utilisateurs."""
    now = time.monotonic()
    ts = _sub_cache.get(user_id)
    if ts is not None and now - ts < SUB_CACHE_TTL_SECONDS:
        return True
    try:
        member = await call_with_retry(
            lambda: bot.get_chat_member(REQUIRED_CHANNEL, user_id),
            label="get_chat_member(abonnement)",
        )
    except Exception as e:
        logger.error(
            "Vérification d'abonnement impossible (le bot est-il admin de %s ?) : %s",
            REQUIRED_CHANNEL, e,
        )
        return True
    ok = member.status in ("member", "administrator", "creator") or (
        member.status == "restricted" and getattr(member, "is_member", False)
    )
    if ok:
        _sub_cache[user_id] = now
    return ok


async def send_join_prompt(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    rows = []
    url = _channel_url()
    if url:
        rows.append([InlineKeyboardButton("📢 الانضمام إلى القناة", url=url)])
    rows.append([InlineKeyboardButton("✅ تحققت من الانضمام", callback_data=SUB_CHECK_CB)])
    await call_with_retry(
        lambda: context.bot.send_message(chat_id, JOIN_MESSAGE, reply_markup=InlineKeyboardMarkup(rows)),
        label="send_message(invite abonnement)",
    )


async def subscription_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bloque toute interaction (messages, boutons, commandes) tant que
    l'utilisateur n'a pas rejoint la chaîne. Les admins ne sont pas concernés."""
    if not REQUIRED_CHANNEL:
        return
    user, chat = update.effective_user, update.effective_chat
    if not user or user.is_bot or not chat or chat.type != "private":
        return
    if user.id in ADMIN_USER_IDS:
        return
    query = update.callback_query
    if query and query.data == SUB_CHECK_CB:
        return   # traité par handle_sub_check
    if await is_subscribed(context.bot, user.id):
        return

    if query:
        try:
            await query.answer("📢 يرجى الانضمام إلى القناة أولًا", show_alert=True)
        except Exception:
            pass
    await send_join_prompt(chat.id, context)
    raise ApplicationHandlerStop


async def handle_sub_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bouton « ✅ تحققت من الانضمام »."""
    query = update.callback_query
    user = update.effective_user
    _sub_cache.pop(user.id, None)   # force une vérification fraîche
    if await is_subscribed(context.bot, user.id):
        await query.answer("✅ شكرًا لانضمامك!")
        try:
            await query.edit_message_text("✅ تم التحقق من انضمامك. يمكنك الآن استخدام البوت.")
        except BadRequest:
            pass
        await show_modules(query.message.chat_id, context)
    else:
        await query.answer("❌ لم يتم العثور على اشتراكك بعد. انضم إلى القناة ثم أعد المحاولة.", show_alert=True)


# ============ CONSTRUCTION DES CLAVIERS (Reply Keyboard) ============

def build_keys_keyboard(keys: list[str], with_back: bool, is_root: bool = False) -> ReplyKeyboardMarkup:
    """Clavier générique pour un niveau de l'arbre. is_root ajoute le bouton
    « 📝 Examen HTML » (uniquement pertinent à la racine, sur la liste des
    modules)."""
    buttons = [[KeyboardButton(k)] for k in keys]
    if is_root:
        buttons.append([KeyboardButton(BTN_EXAM)])
    if with_back:
        buttons.append([KeyboardButton(BTN_BACK)])
    buttons.append([KeyboardButton(BTN_HOME)])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def build_quiz_keyboard() -> ReplyKeyboardMarkup:
    """Clavier affiché pendant l'envoi/la lecture des questions d'une feuille
    (ou pendant la saisie du nombre de questions d'un examen) : « ⬅️ Retour »
    pour revenir choisir un autre dossier/une autre année sans attendre la
    fin, et « 🏠 » pour revenir directement à l'accueil."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_BACK)], [KeyboardButton(BTN_HOME)]], resize_keyboard=True
    )


def build_exam_modules_keyboard() -> ReplyKeyboardMarkup:
    modules = list(QCM_DATA.keys())
    return build_keys_keyboard(modules, with_back=True)


def maybe_reload_qcm_data():
    """Recharge QCM_DATA depuis le disque, mais au maximum une fois toutes les
    QCM_RELOAD_THROTTLE_SECONDS secondes, pour éviter des lectures disque
    répétées à chaque navigation de menu quand beaucoup d'utilisateurs sont
    actifs en même temps."""
    global QCM_DATA, _last_qcm_reload_ts
    now = time.monotonic()
    if now - _last_qcm_reload_ts >= QCM_RELOAD_THROTTLE_SECONDS:
        QCM_DATA = load_qcm_data()
        _last_qcm_reload_ts = now


def abandon_quiz_if_any(context: ContextTypes.DEFAULT_TYPE):
    """Nettoie l'état de navigation/lecture en cours (via ⬅️/🏠), pour repartir
    sur une base propre."""
    chat_data = context.chat_data
    for key in (
        "module", "path", "quiz_questions", "quiz_label",
        "exam_module", "exam_available",
    ):
        chat_data.pop(key, None)


async def show_modules(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    maybe_reload_qcm_data()

    context.chat_data["menu_level"] = "browsing"
    context.chat_data["module"] = None
    context.chat_data["path"] = []

    if not QCM_DATA:
        await call_with_retry(
            lambda: context.bot.send_message(
                chat_id, "Aucune question enregistrée pour le moment.", reply_markup=ReplyKeyboardRemove()
            ),
            label="send_message(aucune question)",
        )
        return

    modules = list(QCM_DATA.keys())
    await call_with_retry(
        lambda: context.bot.send_message(
            chat_id, "📚 Choisis un module :", reply_markup=build_keys_keyboard(modules, with_back=False, is_root=True)
        ),
        label="send_message(modules)",
    )


async def show_node(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu du nœud courant (context.chat_data['module'] + ['path'])."""
    module = context.chat_data.get("module")
    path = context.chat_data.get("path", [])
    node = get_node(QCM_DATA.get(module, {}), path)

    if not isinstance(node, dict) or not node:
        await call_with_retry(
            lambda: context.bot.send_message(chat_id, "⚠️ Ce dossier est vide ou n'existe plus."),
            label="send_message(dossier vide)",
        )
        await show_modules(chat_id, context)
        return

    keys = list(node.keys())
    breadcrumb = " → ".join([module] + path)
    await call_with_retry(
        lambda: context.bot.send_message(
            chat_id, f"📂 {breadcrumb}", reply_markup=build_keys_keyboard(keys, with_back=True)
        ),
        label="send_message(node)",
    )


async def show_exam_module_choice(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    maybe_reload_qcm_data()
    modules = list(QCM_DATA.keys())
    if not modules:
        await call_with_retry(
            lambda: context.bot.send_message(chat_id, "Aucun module disponible pour le moment."),
            label="send_message(exam aucun module)",
        )
        return

    context.chat_data["menu_level"] = "exam_module"
    await call_with_retry(
        lambda: context.bot.send_message(
            chat_id, "📝 Choisis le module pour lequel générer un examen HTML :",
            reply_markup=build_exam_modules_keyboard(),
        ),
        label="send_message(exam choix module)",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bonjour 👋\nChoisis un module avec les boutons ci-dessous pour commencer.\n\n"
        "Les questions s'affichent avec leur bonne réponse cachée sous un spoiler "
        "(appuie dessus pour la révéler).\n"
        f"Sous chaque question, le bouton « {REPORT_BUTTON_LABEL} » permet de signaler une question à revoir."
    )
    await show_modules(update.effective_chat.id, context)


async def qcm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_modules(update.effective_chat.id, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère les appuis sur les boutons du clavier persistant (Reply Keyboard)."""
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    if text == BTN_HOME:
        abandon_quiz_if_any(context)
        await show_modules(chat_id, context)
        return

    level = context.chat_data.get("menu_level", "browsing")

    if text == BTN_BACK:
        if level == "exam_module":
            await show_modules(chat_id, context)
            return

        if level == "exam_count":
            context.chat_data.pop("exam_module", None)
            context.chat_data.pop("exam_available", None)
            await show_exam_module_choice(chat_id, context)
            return

        # level == "browsing" ou "quiz" (retour depuis la lecture des
        # questions vers le dossier parent, ex: choisir une autre année)
        context.chat_data["menu_level"] = "browsing"
        context.chat_data.pop("quiz_questions", None)
        context.chat_data.pop("quiz_label", None)
        module = context.chat_data.get("module")
        path = context.chat_data.get("path", [])
        if path:
            context.chat_data["path"] = path[:-1]
            await show_node(chat_id, context)
        elif module is not None:
            context.chat_data["module"] = None
            await show_modules(chat_id, context)
        else:
            await show_modules(chat_id, context)
        return

    if level == "quiz":
        return  # on ignore le texte libre pendant l'envoi des questions

    if level == "exam_module":
        modules = list(QCM_DATA.keys())
        if text in modules:
            module = text
            available = collect_module_questions(module)
            if not available:
                await call_with_retry(
                    lambda: context.bot.send_message(
                        chat_id, f"⚠️ Aucune question valide dans le module « {module} »."
                    ),
                    label="send_message(exam module vide)",
                )
                await show_modules(chat_id, context)
                return

            context.chat_data["exam_module"] = module
            context.chat_data["exam_available"] = len(available)
            context.chat_data["menu_level"] = "exam_count"

            await call_with_retry(
                lambda: context.bot.send_message(
                    chat_id,
                    f"🔢 Combien de questions veux-tu dans l'examen « {module} » ?\n"
                    f"(maximum disponible : {len(available)})",
                    reply_markup=build_quiz_keyboard(),
                ),
                label="send_message(exam demande nombre)",
            )
        else:
            await call_with_retry(
                lambda: context.bot.send_message(chat_id, "Choisis un module parmi les boutons ci-dessous."),
                label="send_message(exam choix module invalide)",
            )
        return

    if level == "exam_count":
        module = context.chat_data.get("exam_module")
        max_available = context.chat_data.get("exam_available", 0)

        if not text.isdigit():
            await call_with_retry(
                lambda: context.bot.send_message(chat_id, "Merci d'entrer un nombre entier positif."),
                label="send_message(exam nombre invalide)",
            )
            return

        n = int(text)
        if n <= 0 or n > max_available:
            await call_with_retry(
                lambda: context.bot.send_message(
                    chat_id, f"Merci d'entrer un nombre entre 1 et {max_available}."
                ),
                label="send_message(exam nombre hors limites)",
            )
            return

        all_questions = collect_module_questions(module)
        selected = random.sample(all_questions, n)
        exam_html = build_exam_html(module, selected)
        file_bytes = exam_html.encode("utf-8")

        safe_name = re.sub(r"[^\w\-]+", "_", module, flags=re.UNICODE).strip("_") or "module"
        filename = f"examen_{safe_name}_{n}q.html"

        await call_with_retry(
            lambda: context.bot.send_document(
                chat_id,
                document=InputFile(io.BytesIO(file_bytes), filename=filename),
                caption=f"📝 Examen « {module} » — {n} question(s) — note sur {EXAM_TOTAL_POINTS}",
            ),
            label="send_document(exam)",
        )

        context.chat_data.pop("exam_module", None)
        context.chat_data.pop("exam_available", None)
        await show_modules(chat_id, context)
        return

    # level == "browsing" : navigation dans l'arbre (profondeur libre)
    module = context.chat_data.get("module")
    path = context.chat_data.get("path", [])

    if module is None:
        if text == BTN_EXAM:
            await show_exam_module_choice(chat_id, context)
            return
        if text in QCM_DATA:
            context.chat_data["module"] = text
            context.chat_data["path"] = []
            await show_node(chat_id, context)
        else:
            await call_with_retry(
                lambda: context.bot.send_message(chat_id, "Choisis un module parmi les boutons ci-dessous."),
                label="send_message(choix module invalide)",
            )
        return

    node = get_node(QCM_DATA.get(module, {}), path)
    if not isinstance(node, dict) or text not in node:
        await call_with_retry(
            lambda: context.bot.send_message(chat_id, "Choisis une option parmi les boutons ci-dessous."),
            label="send_message(choix option invalide)",
        )
        return

    child = node[text]
    new_path = path + [text]

    if isinstance(child, list):
        # feuille atteinte : on envoie toutes les questions de cette liste
        context.chat_data["path"] = new_path
        context.chat_data["quiz_questions"] = child
        context.chat_data["quiz_label"] = " / ".join([module] + new_path)
        context.chat_data["menu_level"] = "quiz"

        await call_with_retry(
            lambda: context.bot.send_message(
                chat_id,
                f"🚀 « {context.chat_data['quiz_label']} » — envoi de toutes les questions...",
                reply_markup=build_quiz_keyboard(),
            ),
            label="send_message(debut quiz)",
        )
        await send_all_questions(chat_id, context)
    else:
        # encore un dossier : on descend d'un niveau
        context.chat_data["path"] = new_path
        await show_node(chat_id, context)


async def send_all_questions(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Envoie toutes les questions valides d'une feuille d'un coup, sous forme
    de messages texte (question + options + bonne réponse en spoiler). Les
    envois passent par le rate limiter global et réessaient automatiquement en
    cas d'erreur transitoire (flood control Telegram), ce qui évite de perdre
    des questions quand beaucoup d'utilisateurs sont actifs en même temps."""
    chat_data = context.chat_data
    questions = chat_data.get("quiz_questions", [])

    if not questions:
        await call_with_retry(
            lambda: context.bot.send_message(chat_id, "⚠️ Aucune question ici."),
            label="send_message(quiz vide)",
        )
        await show_modules(chat_id, context)
        return

    skipped = []
    failed = []
    sent_count = 0

    for idx, q in enumerate(questions):
        if not is_valid_question(q):
            skipped.append(idx + 1)
            logger.error("Question invalide dans '%s' index %s : %s", chat_data.get("quiz_label"), idx, q)
            continue

        text_q = build_question_message(idx, len(questions), q)
        if len(text_q) > TG_MESSAGE_MAX:
            # Message trop long pour Telegram (rare, en général à cause d'une
            # longue explication) : on l'envoie sans l'explication.
            q_sans_explication = dict(q)
            q_sans_explication.pop("explanation", None)
            text_q = build_question_message(idx, len(questions), q_sans_explication)

        report_token = register_question_context(
            chat_id, q, set(q["correct_option_ids"]),
            module=chat_data.get("module"),
            path=chat_data.get("path", []),
            quiz_label=chat_data.get("quiz_label"),
        )
        markup = build_question_button_markup(report_token)

        try:
            sent = await call_with_retry(
                lambda: context.bot.send_message(
                    chat_id, text_q, parse_mode="HTML", reply_markup=markup,
                ),
                label=f"send_message(question q{idx + 1})",
            )
        except Exception as e:
            failed.append(idx + 1)
            logger.error(
                "Échec d'envoi de la question '%s' index %s : %s",
                chat_data.get("quiz_label"), idx, e,
            )
            continue

        info = question_context.get(report_token)
        if info is not None:
            info["message_id"] = sent.message_id
            info["base_text"] = text_q

        sent_count += 1
        await asyncio.sleep(SEND_DELAY_SECONDS)

    if skipped:
        await call_with_retry(
            lambda: context.bot.send_message(
                chat_id,
                "⚠️ Questions ignorées à cause d'un problème dans leurs données : " + ", ".join(map(str, skipped)),
            ),
            label="send_message(skipped)",
        )

    if failed:
        await call_with_retry(
            lambda: context.bot.send_message(
                chat_id,
                "⚠️ Questions non envoyées (échec d'envoi) : " + ", ".join(map(str, failed)),
            ),
            label="send_message(failed)",
        )

    if sent_count == 0:
        await call_with_retry(
            lambda: context.bot.send_message(chat_id, "⚠️ Aucune question valide ici."),
            label="send_message(aucune valide)",
        )
        await show_modules(chat_id, context)
        return

    # Fin de la lecture : on repasse en navigation normale (⬅️ Retour permet
    # de choisir un autre dossier/une autre année, 🏠 ramène à l'accueil).
    context.chat_data["menu_level"] = "browsing"
    await call_with_retry(
        lambda: context.bot.send_message(
            chat_id,
            f"✅ « {chat_data.get('quiz_label', '')} » — {sent_count} question(s) envoyée(s).\n"
            "Appuie sur le spoiler de chaque question pour voir la bonne réponse.",
            reply_markup=build_quiz_keyboard(),
        ),
        label="send_message(fin envoi questions)",
    )


async def periodic_question_context_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Job récurrent : purge les entrées question_context expirées pour éviter
    une fuite mémoire quand le bot tourne longtemps avec beaucoup d'utilisateurs,
    et sauvegarde la liste des utilisateurs si elle a changé."""
    cleanup_question_context()
    now = time.monotonic()
    for uid in [u for u, ts in _sub_cache.items() if now - ts > SUB_CACHE_TTL_SECONDS]:
        _sub_cache.pop(uid, None)
    if _users_dirty:
        _save_users()


def build_application() -> Application:
    """Construit l'Application PTB (handlers + job queue), sans la démarrer.
    Utilisée à la fois par le mode polling (test local) et le mode webhook
    (déploiement PythonAnywhere)."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "⚠️  BOT_TOKEN manquant : définis la variable d'environnement BOT_TOKEN "
            "avant de lancer le bot (voir DEPLOY.md)."
        )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)   # traite les updates de différents utilisateurs en parallèle
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .get_updates_connect_timeout(30)
        .get_updates_read_timeout(30)
        .build()
    )

    # --- Groupes négatifs : s'exécutent AVANT les handlers normaux (groupe 0) ---
    # -6 : enregistrement des utilisateurs (ne bloque jamais la suite)
    # -5 : vérification d'abonnement à la chaîne (bloque si non abonné)
    # -3 : réponse d'un utilisateur à un message de l'admin
    # -2 : saisie de l'admin (recherche / écriture à un utilisateur)
    app.add_handler(TypeHandler(Update, track_user), group=-6)
    app.add_handler(TypeHandler(Update, subscription_gate), group=-5)
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND & filters.ChatType.PRIVATE, user_reply_input),
        group=-3,
    )
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, admin_input), group=-2)

    # --- Handlers normaux ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("qcm", qcm_command))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(handle_sub_check, pattern=rf"^{SUB_CHECK_CB}$"))
    app.add_handler(CallbackQueryHandler(handle_report_button, pattern=rf"^{REPORT_CALLBACK_PREFIX}"))
    app.add_handler(CallbackQueryHandler(handle_report_type, pattern=rf"^{REPORT_TYPE_PREFIX}"))
    app.add_handler(CallbackQueryHandler(handle_report_cancel, pattern=rf"^{REPORT_CANCEL_PREFIX}"))
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=rf"^{ADM}"))
    app.add_handler(CallbackQueryHandler(handle_reply_button, pattern=rf"^{REPLY_CB}"))
    app.add_handler(CallbackQueryHandler(handle_reply_cancel, pattern=rf"^{REPLY_CANCEL}$"))

    app.job_queue.run_repeating(periodic_question_context_cleanup, interval=QUESTION_CONTEXT_CLEANUP_INTERVAL, first=QUESTION_CONTEXT_CLEANUP_INTERVAL)
    return app


def main():
    """Mode polling classique : pour tester le bot en local sur ta machine
    (python bot.py). Ne pas utiliser sur PythonAnywhere (voir webhook plus bas)."""
    app = build_application()
    print("🤖 Le bot est lancé en mode polling (local)... (Ctrl+C pour arrêter)")
    app.run_polling()


# ============ MODE WEBHOOK (PythonAnywhere / tout serveur WSGI) ============
# create_flask_app() n'est appelée que par un serveur WSGI (voir
# wsgi_pythonanywhere.py). À l'import, bot.py n'a AUCUN effet de bord : c'est
# ce qui permet de l'importer depuis render_app.py (Render) sans démarrer
# quoi que ce soit.
def create_flask_app():
    from flask import Flask, request

    tg_app = build_application()

    # PTB (asyncio) a besoin d'une boucle d'événements persistante : on en crée
    # une une seule fois au chargement du module, et on la réutilise pour
    # chaque requête webhook (au lieu d'asyncio.run() qui en recrée une à
    # chaque fois et casserait les objets internes de l'Application).
    _webhook_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_webhook_loop)
    _webhook_loop.run_until_complete(tg_app.initialize())
    _webhook_loop.run_until_complete(tg_app.start())  # démarre aussi le job_queue

    flask_app = Flask(__name__)

    # Le token dans l'URL sert de "secret" basique : seul quelqu'un connaissant
    # le token peut deviner l'URL du webhook.
    WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

    @flask_app.route(WEBHOOK_PATH, methods=["POST"])
    def telegram_webhook():
        update = Update.de_json(request.get_json(force=True), tg_app.bot)
        _webhook_loop.run_until_complete(tg_app.process_update(update))
        return "OK"

    @flask_app.route("/", methods=["GET"])
    def health_check():
        return "✅ Le bot QCM est en ligne (mode webhook)."

    return flask_app


if __name__ == "__main__":
    main()