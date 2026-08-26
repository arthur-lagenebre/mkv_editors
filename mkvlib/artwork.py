"""Vignettes de dossier (folder.jpg) téléchargées depuis TMDB.

Windows et la plupart des lecteurs multimedias utilisent folder.jpg comme illustration du dossier ; on la prend en anglais, où les affiches sont mieux fournies que dans les autres langues.
"""

from pathlib import Path

from .tmdb import TmdbError

POSTER_SIZE = "w500"       # les affiches sont en portrait
ARTWORK_LANG = "en-US"


def english_poster(fetch, fallback):
    """poster_path de la version anglaise, avec repli sur celui déjà récupère."""
    try:
        return fetch().get("poster_path") or fallback
    except TmdbError:
        return fallback


def write_poster(poster_path, folder, apply, tmdb, size=POSTER_SIZE):
    """Écrit folder.jpg dans `folder`. Retourne un compte rendu affichable."""
    if not poster_path:
        return "pas d'affiche TMDB"
    dest = Path(folder) / "folder.jpg"
    if not apply:
        return f"ecrirait {dest.name}"
    try:
        tmdb.save_image(poster_path, size, dest)
        return "folder.jpg ecrit"
    except (TmdbError, OSError) as e:
        return f"echec ({e})"
