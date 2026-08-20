"""Utilitaires de ligne de commande : console, fichier .env, cle TMDB."""

import os
import sys
from pathlib import Path


def setup_console():
    """Force stdout/stderr en UTF-8 tolerant.

    Sous Windows la console est en cp1252 : afficher un titre TMDB japonais ou
    cyrillique y leve UnicodeEncodeError et interrompt le script en plein
    travail (un renommage a moitie applique, par exemple). Avec errors=replace
    le caractere s'affiche mal, mais le traitement va au bout.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):        # flux redirige, non reconfigurable
            pass


def load_dotenv(filename=".env"):
    """Charge un fichier .env (lignes CLE=valeur) dans les variables d'environnement.

    Le fichier est cherche en remontant depuis le dossier de ce module, puis
    depuis le dossier courant ; on s'arrete au premier trouve. Les variables
    deja definies dans l'environnement ne sont jamais ecrasees.
    """
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for folder in (start, *start.parents):
            path = folder / filename
            if not path.is_file():
                continue
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
                    os.environ.setdefault(name, value)
            return path
    return None


NO_KEY_MESSAGE = ("Aucune cle TMDB. Renseigne la ligne TMDB_KEY=... du fichier .env "
                  "(voir .env.example), la variable d'environnement TMDB_API_KEY, "
                  "ou la constante TMDB_KEY en haut du fichier.")


def resolve_tmdb_key(fallback=""):
    """Cle TMDB par priorite : .env > TMDB_API_KEY > TMDB_KEY > constante du script.

    Quitte avec un message explicite si aucune cle n'est disponible.
    """
    load_dotenv()
    key = os.environ.get("TMDB_API_KEY") or os.environ.get("TMDB_KEY") or fallback
    if not key:
        sys.exit(NO_KEY_MESSAGE)
    return key


def mode_label(args, simulation="rien ne sera ecrit ; ajoute --apply pour appliquer"):
    """Libelle du mode courant, pour la banniere affichee au demarrage."""
    if getattr(args, "verify", False):
        return "VERIFICATION (aucune ecriture)"
    return "APPLICATION" if args.apply else f"SIMULATION ({simulation})"
