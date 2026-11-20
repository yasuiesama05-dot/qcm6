# -*- coding: utf-8 -*-
"""
push_qcm.py — Envoie les fichiers de questions modifiés vers GitHub.

Usage (le plus simple) : double-clique sur « push_qcm.bat ».
Ou depuis un terminal, dans le dossier du projet :

    python push_qcm.py              # détecte data/*.json modifiés, valide, commit, push
    python push_qcm.py --dry-run    # montre seulement ce qui serait envoyé
    python push_qcm.py data/cardio.json data/hématologie.json
    python push_qcm.py -m "Mon message de commit"
    python push_qcm.py -y           # sans demander de confirmation

Après le push, Render redéploie automatiquement le bot (1 à 3 minutes).

Au premier envoi, Git peut ouvrir une fenêtre pour se connecter à GitHub
(Git Credential Manager). La connexion est ensuite mémorisée par Windows.
"""

import argparse
import json
import os
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MAX_OPTIONS = 5


def git(*args, check=False):
    """Lance une commande git et renvoie (code, sortie)."""
    result = subprocess.run(
        ["git", *args],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout or "") + (result.stderr or "")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}\n{output.strip()}")
    return result.returncode, output


def changed_data_files():
    """Liste les fichiers data/*.json modifiés / nouveaux dans le dépôt."""
    code, out = git("status", "--porcelain", "--", "data")
    files = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1].encode().decode("unicode_escape")
        path = path.replace("\\", "/")
        if path.lower().endswith(".json"):
            files.append(path)
    return sorted(set(files))


def iter_questions(node):
    if isinstance(node, list):
        yield from (q for q in node if isinstance(q, dict))
    elif isinstance(node, dict):
        for value in node.values():
            yield from iter_questions(value)


def validate_file(path):
    """Vérifie le JSON et la structure ; renvoie (nb_questions, [erreurs])."""
    errors = []
    full = os.path.join(BASE_DIR, path)
    try:
        with open(full, "r", encoding="utf-8-sig") as f:
            content = json.load(f)
    except json.JSONDecodeError as e:
        return 0, [f"JSON invalide ligne {e.lineno}, colonne {e.colno} : {e.msg}"]
    except OSError as e:
        return 0, [f"Impossible de lire le fichier : {e}"]

    if not isinstance(content, dict) or "module" not in content or "lessons" not in content:
        return 0, ['Structure attendue : {"module": "...", "lessons": {...}}']

    total = 0
    for q in iter_questions(content.get("lessons")):
        total += 1
        text = (q.get("question") or "").strip()
        label = text[:60] or "(question sans texte)"
        options = q.get("options") or []
        ids = q.get("correct_option_ids") or []
        if not text:
            errors.append(f"{label} → texte de question vide")
        if not isinstance(options, list) or not (2 <= len(options) <= MAX_OPTIONS):
            errors.append(f"{label} → il faut 2 à {MAX_OPTIONS} options")
            continue
        if any(not str(o).strip() for o in options):
            errors.append(f"{label} → une option est vide")
        if not ids:
            errors.append(f"{label} → aucune bonne réponse")
        elif any(not isinstance(i, int) or not (0 <= i < len(options)) for i in ids):
            errors.append(f"{label} → correct_option_ids hors limites")
    return total, errors


def confirm(question):
    try:
        return input(f"{question} [o/N] ").strip().lower() in ("o", "oui", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def main():
    parser = argparse.ArgumentParser(description="Pousse les fichiers data/*.json modifiés vers GitHub.")
    parser.add_argument("files", nargs="*", help="fichiers précis à envoyer (par défaut : tous les modifiés)")
    parser.add_argument("-m", "--message", help="message de commit")
    parser.add_argument("-y", "--yes", action="store_true", help="ne pas demander de confirmation")
    parser.add_argument("--dry-run", action="store_true", help="afficher sans envoyer")
    parser.add_argument("--force", action="store_true", help="envoyer même si la validation échoue")
    args = parser.parse_args()

    os.chdir(BASE_DIR)

    code, branch = git("rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    if code != 0 or branch in ("", "HEAD"):
        print("❌ Ce dossier n'est pas sur une branche Git valide.")
        return 1

    files = args.files or changed_data_files()
    if not files:
        print("✅ Aucun fichier de questions modifié : rien à envoyer.")
        return 0

    print("📄 Fichiers détectés :")
    has_errors = False
    for path in files:
        total, errors = validate_file(path)
        if errors:
            has_errors = True
            print(f"  ❌ {path} — {total} questions, {len(errors)} problème(s) :")
            for err in errors[:10]:
                print(f"       • {err}")
            if len(errors) > 10:
                print(f"       … et {len(errors) - 10} autre(s)")
        else:
            print(f"  ✅ {path} — {total} questions valides")

    if has_errors and not args.force:
        print("\n⛔ Envoi annulé : corrige les problèmes ci-dessus.")
        print("   (le bot ignorerait ces questions ; --force pour envoyer quand même)")
        return 1

    print("\n📊 Résumé des changements :")
    git("add", "--", *files)
    code, out = git("diff", "--cached", "--stat", "--", *files)
    print(out.strip() or "  (aucun changement réel)")

    if args.dry_run:
        print("\n🔎 Mode --dry-run : rien n'a été envoyé.")
        git("reset", "--", *files)
        return 0

    if not args.yes and not confirm("\nEnvoyer ces fichiers sur GitHub ?"):
        print("Annulé.")
        git("reset", "--", *files)
        return 0

    message = args.message or f"Mise à jour des QCM — {time.strftime('%d/%m/%Y %H:%M')}"
    code, out = git("commit", "-m", message, "--", *files)
    if code != 0:
        print(f"❌ Le commit a échoué :\n{out.strip()}")
        return 1
    print(f"✅ Commit créé : {message}")

    print("🚀 Envoi vers GitHub…")
    code, out = git("push", "origin", branch)
    if code != 0 and ("rejected" in out or "non-fast-forward" in out):
        print("ℹ️  De nouveaux changements existent sur GitHub, je les récupère d'abord…")
        code2, out2 = git("pull", "--rebase", "origin", branch)
        if code2 != 0:
            git("rebase", "--abort")
            print("❌ Conflit impossible à résoudre automatiquement :\n" + out2.strip())
            print("   Ton commit local est conservé. Demande de l'aide pour fusionner.")
            return 1
        code, out = git("push", "origin", branch)

    if code != 0:
        print("❌ L'envoi a échoué :\n" + out.strip())
        if "Authentication" in out or "could not read Username" in out or "403" in out:
            print("\n👉 Vérifie ta connexion GitHub (Git Credential Manager).")
        return 1

    print("🎉 Envoyé ! Render va redéployer le bot automatiquement (1 à 3 min).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)
