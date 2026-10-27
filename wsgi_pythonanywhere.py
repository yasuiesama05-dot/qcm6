# -*- coding: utf-8 -*-
"""
Fichier WSGI pour PythonAnywhere (alternative à Render).

Dans l'onglet Web de PythonAnywhere, le fichier WSGI doit contenir :

    import sys
    sys.path.insert(0, "/home/TON_NOM/qcm_project")
    os.environ["BOT_TOKEN"] = "xxxx"
    from wsgi_pythonanywhere import application

(voir DEPLOY.md pour la procédure complète)
"""

from bot import create_flask_app

application = create_flask_app()
