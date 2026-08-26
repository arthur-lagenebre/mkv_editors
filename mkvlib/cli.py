"""Utilitaires de ligne de commande : console, fichier .env, clé TMDB."""

import sys
from pathlib import Path


def setup_console():
    """Force stdout/stderr en UTF-8 tolérant.

    Sous Windows la console est en cp1252 : afficher un titre TMDB japonais ou cyrillique y leve UnicodeEncodeError et interrompt le script en plein travail (un renommage à moitié applique, par exemple). Avec errors=replace le caractère s'affiche mal, mais le traitement va au bout.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):        # flux redirige, non reconfigurable
            pass


def read_dotenv(filename=".env"):
    """Valeurs d'un fichier .env (lignes CLÉ=valeur), en dictionnaire.

    Le fichier est cherché en remontant depuis le dossier de ce module, puis depuis le dossier courant ; on s'arrête au premier trouvé. Rien n'est écrit dans les variables d'environnement : le .env est la seule source de la clé, et mieux vaut que ça se voie ici plutôt que de se deviner ailleurs.
    """
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for folder in (start, *start.parents):
            path = folder / filename
            if not path.is_file():
                continue
            valeurs = {}
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name, value = name.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if name:
                    valeurs.setdefault(name, value)      # la première ligne l'emporte
            return valeurs
    return {}


NO_KEY_MESSAGE = ("Aucune cle TMDB. Renseigne la ligne TMDB_KEY=... du fichier .env "
                  "a la racine du depot (voir .env.example).")


def resolve_tmdb_key():
    """Clé TMDB lue dans le fichier .env, seule source acceptee.

    Quitte avec un message explicite si le fichier manque ou ne la donne pas.
    """
    key = read_dotenv().get("TMDB_KEY", "").strip()
    if not key:
        sys.exit(NO_KEY_MESSAGE)
    return key


def check_dir(path):
    """Vérifie que --dir désigne un dossier existant, et s'arrête clairement sinon.

    Une faute de frappe dans le chemin est l'erreur la plus courante : sans ce contrôle, elle ressort soit en pile d'appels, soit - pire - en "aucun fichier trouve", qui ressemble à une médiathèque vide.
    """
    dossier = Path(path)
    if not dossier.exists():
        sys.exit(f"Dossier introuvable : {dossier}")
    if not dossier.is_dir():
        sys.exit(f"--dir attend un dossier, pas un fichier : {dossier}")
    return dossier


ASK_SKIP, ASK_STOP = "ignorer", "arreter"


def can_ask():
    """Vrai si une question à une chance d'obtenir une réponse.

    Sortie redirigée vers un fichier, exécution dans un CI, entrée fermée : la question ne serait vue par personne et le script attendrait indéfiniment. Mieux vaut alors laisser le cas de côté que de bloquer un passage entier.
    """
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):         # flux ferme ou remplace
        return False


def ask_choice(nombre, defaut=0):
    """Indice choisi parmi `nombre` propositions, ou ASK_SKIP / ASK_STOP.

    Entrée vide = le défaut. Une réponse incomprise repose la question plutôt que de décider à la place de quelqu'un - c'est tout l'intérêt de demander.
    """
    while True:
        try:
            reponse = input(f"      choix [{defaut + 1}] : ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return ASK_STOP
        if not reponse:
            return defaut
        if reponse in ("i", "ignorer"):
            return ASK_SKIP
        if reponse in ("q", "quitter", "arreter"):
            return ASK_STOP
        if reponse.isdigit() and 1 <= int(reponse) <= nombre:
            return int(reponse) - 1
        print(f"      reponse non comprise : un numero de 1 a {nombre}, "
              "i pour laisser de cote, q pour arreter les questions.")


def mode_label(args, simulation="rien ne sera ecrit ; ajoute --apply pour appliquer"):
    """Libellé du mode courant, pour la bannière affichee au démarrage."""
    if getattr(args, "verify", False):
        return "VERIFICATION (aucune ecriture)"
    return "APPLICATION" if args.apply else f"SIMULATION ({simulation})"
