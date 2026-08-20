"""Association d'un nom de dossier a une fiche TMDB.

Films et series posent la meme question - "de quoi parle ce dossier ?" - et
meritent la meme reponse : le premier resultat, mais avec les doutes affiches
plutot qu'avales.
"""

from difflib import SequenceMatcher
from pathlib import Path

from . import naming
from .tmdb import TmdbError

# Un titre trouve moins ressemblant que ca a la recherche merite d'etre verifie.
MIN_SIMILARITE = 0.6


def _norm(s):
    return " ".join((s or "").lower().split())


def describe(item, key="title", date_key="release_date"):
    """'Inception (2010) [id 27205]' — comment on montre un resultat TMDB."""
    return (f"{item.get(key)} ({(item.get(date_key) or '?')[:4]}) "
            f"[id {item.get('id')}]")


def pick_result(results, query, key="title", date_key="release_date"):
    """(resultat, remarques). Le premier resultat TMDB, avec les doutes rendus visibles.

    TMDB classe par popularite : quand deux fiches portent le meme titre (remake,
    reboot) ou quand le titre trouve n'a plus grand-chose a voir avec la recherche,
    le premier resultat n'est pas forcement le bon. Autant le dire.
    """
    best = results[0]
    notes = []
    if len(results) > 1 and _norm(results[1].get(key)) == _norm(best.get(key)):
        notes.append(f"titre partage avec {describe(results[1], key, date_key)} "
                     "-> verifie l'annee, ou force --tmdb-id")
    if SequenceMatcher(None, _norm(query), _norm(best.get(key))).ratio() < MIN_SIMILARITE:
        notes.append("titre trouve eloigne de la recherche -> a verifier")
    return best, notes


def series_query(directory):
    """(titre, annee) a chercher sur TMDB pour un dossier de serie.

    Si --dir pointe sur un dossier de saison, c'est le dossier parent qui porte
    le nom de la serie.
    """
    path = Path(directory).resolve()
    if naming.season_number(path.name) is not None:
        path = path.parent
    title, year, _ = naming.parse_title_year(path.name)
    return title, year


def resolve_show_id(tmdb, directory, forced=None):
    """Id TMDB de la serie : celui force par --tmdb-id, sinon une recherche sur
    le nom du dossier. Retourne None (en expliquant) si rien ne correspond."""
    if forced:
        return forced
    title, year = series_query(directory)
    if not title:
        print("Nom de dossier inexploitable pour une recherche TMDB.")
        return None
    try:
        results = tmdb.search_tv(title, year)
        if not results and year:
            results = tmdb.search_tv(title, None)
    except TmdbError as e:
        print(f"Echec de la recherche TMDB : {e}")
        return None
    if not results:
        print(f"Aucune serie TMDB pour '{title}'" + (f" ({year})" if year else "") + ".")
        return None
    best, notes = pick_result(results, title, key="name", date_key="first_air_date")
    print(f"recherche : '{title}'" + (f" ({year})" if year else "")
          + f" -> {describe(best, 'name', 'first_air_date')}")
    for note in notes:
        print(f"/!\\ {note}")
    return best["id"]
