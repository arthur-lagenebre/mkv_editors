"""Association d'un nom de dossier a une fiche TMDB.

Films et series posent la meme question - "de quoi parle ce dossier ?" - et
meritent la meme reponse : le premier resultat, mais avec les doutes affiches
plutot qu'avales.
"""

import re
from dataclasses import dataclass, field
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


def _ratio(query, title):
    """Ressemblance entre ce qu'on a cherche et ce que TMDB a rendu."""
    return SequenceMatcher(None, _norm(query), _norm(title)).ratio()


def _mots(s):
    return re.sub(r"[^0-9a-zà-ÿ ]+", " ", (s or "").lower()).split()


def contient(texte, fragment):
    """`texte` reprend-il tous les mots de `fragment`, a la suite ?

    Comparer des sous-chaines ne marche pas a cette echelle : un dossier nomme
    "A" se retrouverait dans "Autre", et "V" dans "Vendetta". Ce sont les MOTS
    qui doivent correspondre.
    """
    mots, cherches = _mots(texte), _mots(fragment)
    n = len(cherches)
    return n > 0 and any(mots[i:i + n] == cherches for i in range(len(mots) - n + 1))


# Une fiche obscure qui porte pile le bon titre reste une fiche obscure. Sans ce
# garde-fou, "The Fast and the Furious" (1954, 41 votes) prend la place de celui
# de 2001 (11 029 votes), et "Alien vs Predator" (2022, 2 votes) celle de 2004.
# Le nombre de votes TMDB est le seul signal de notoriete dont on dispose.
#
# Mesure sur les deux mediatheques : les fiches a garder pesent de 20% a 270% de
# la tete de liste, celles a rejeter moins de 1%. Le seuil tient dans ce fosse.
MIN_VOTES = 50
PART_TITRE_EXACT = 10     # un titre exact est une preuve faible : il faut du poids
PART_SAGA = 20            # un titre qui porte saga ET sous-titre prouve deja plus


def _votes(item):
    return (item or {}).get("vote_count") or 0


def credible(candidat, reference, part=PART_TITRE_EXACT):
    """Le candidat pese-t-il assez face a la fiche que TMDB classe en premier ?"""
    return _votes(candidat) >= max(MIN_VOTES, _votes(reference) / part)


def _search(tmdb, title, year):
    """Recherche TMDB sur un titre : avec l'annee, puis sans si elle ne donne rien."""
    results = tmdb.search_movie(title, year)
    if not results and year:
        results = tmdb.search_movie(title, None)
    return results


MAX_CONTEXTS = 2          # dossier parent, puis grand-parent : au-dela, on s'egare


def usable_contexts(contexts, title, results, key="title"):
    """Dossiers qui peuvent encore apprendre quelque chose a la recherche.

    Un dossier deja present dans le titre cherche n'apporte rien ("X-Men/01 -
    X-Men" chercherait "X-Men X-Men"). Et si le film trouve porte deja le nom de
    la saga, la recherche a compris toute seule : inutile d'insister.
    """
    if isinstance(contexts, str):
        contexts = [contexts] if contexts else []
    trouve = results[0].get(key) if results else ""
    utiles = []
    for context in contexts:
        # Le dossier repete le titre ("X-Men/01 - X-Men"), ou le contient
        # ("Les chroniques de Riddick/3 - Riddick") : il n'apprend rien.
        if not context or contient(title, context) or contient(context, title):
            continue
        if trouve and contient(trouve, context):
            return []
        utiles.append(context)
    return utiles[:MAX_CONTEXTS]


def search_with_context(tmdb, title, year, contexts=(), key="title"):
    """(resultats, requete retenue) : le titre seul, les dossiers parents en renfort.

    Le nom d'un fichier de saga n'est souvent que le sous-titre du film, et ce
    sous-titre appartient a un autre film ailleurs sur TMDB : "Apocalypse" rend
    "Amour Apocalypse", "Vendetta" rend "V pour Vendetta". Le dossier, lui, sait
    de quelle saga il s'agit.

    Le renfort ne l'emporte qu'a deux conditions, faute de quoi il ferait pire :
      - le titre trouve contient A LA FOIS le dossier et ce qu'on cherchait,
        signe qu'on est tombe sur le bon film de la bonne saga ;
      - ou bien le titre seul ne ressemblait a rien et le renfort fait mieux.
    Un nom de saga que TMDB ignore ("DCEU Man of Steel") ne rend rien et laisse
    donc le resultat nu en place.
    """
    results = _search(tmdb, title, year)
    score = _ratio(title, results[0].get(key)) if results else 0.0
    for context in usable_contexts(contexts, title, results, key):
        renfort_q = f"{context} {title}"
        renfort = _search(tmdb, renfort_q, year)
        if not renfort:
            continue
        trouve = renfort[0].get(key) or ""
        saga = contient(trouve, context) and contient(trouve, title)
        mieux = score < MIN_SIMILARITE and _ratio(renfort_q, trouve) > score
        # Sans le controle de notoriete, "Les chroniques de Riddick/1 - Pitch
        # Black" troquerait Pitch Black (4 914 votes) contre un court metrage
        # d'animation de la saga (99 votes) qui, lui, porte les deux noms.
        if (saga or mieux) and (not results
                                or credible(renfort[0], results[0], PART_SAGA)):
            return renfort, renfort_q
    return results, title


def pick_result(results, query, key="title", date_key="release_date"):
    """(resultat, remarques). La fiche retenue, avec les doutes rendus visibles.

    TMDB classe par POPULARITE, pas par pertinence : chercher "Blade" rend "Blade
    II", "Predator" rend "Predator: Badlands", "Avengers" rend un film de 2026.
    Une fiche qui porte EXACTEMENT le titre cherche passe donc devant - c'est le
    seul signal dont on dispose qui ne depend pas de la mode du moment.

    Encore faut-il que cette fiche existe vraiment : un titre exact porte par un
    inconnu ne vaut pas mieux qu'un classement, d'ou le controle de notoriete.

    Restent les doutes qu'on ne peut pas lever : deux fiches du meme titre
    (remake, reboot), ou un titre trouve qui n'a plus grand-chose a voir.
    """
    best = results[0]
    exact = next((m for m in results if _norm(m.get(key)) == _norm(query)), None)
    if exact is not None and credible(exact, best):
        best = exact
    notes = []
    jumeau = next((m for m in results
                   if m is not best and _norm(m.get(key)) == _norm(best.get(key))), None)
    if jumeau is not None:
        notes.append(f"titre partage avec {describe(jumeau, key, date_key)} "
                     "-> verifie l'annee, ou force --tmdb-id")
    if _ratio(query, best.get(key)) < MIN_SIMILARITE:
        notes.append("titre trouve eloigne de la recherche -> a verifier")
    return best, notes


# Deux fiches qui ecrivent le meme titre autrement ne se departagent pas toutes
# seules : "Les 4 Fantastiques" et "Les Quatre Fantastiques" sont le meme titre,
# et TMDB en propose trois. Une SUITE, elle, rallonge le titre ("Iron Man 2") :
# c'est ce qui separe une vraie hesitation d'une simple saga.
VARIANTE_MIN = 0.75
VARIANTE_MAX = 6          # au-dela, ce ne sont plus des concurrents credibles


def rival_versions(query, results, key="title"):
    """Fiches qui ecrivent le meme titre que la recherche, mais autrement.

    Meme nombre de mots et forte ressemblance, sans etre un prolongement de la
    recherche. Mesure faite sur 101 films : 6 hesitations levees, dont un
    making-of pris pour son film.
    """
    rivaux = []
    for item in results[1:VARIANTE_MAX]:
        titre = item.get(key) or ""
        if _norm(titre).startswith(_norm(query)) or len(_mots(titre)) != len(_mots(query)):
            continue
        if _ratio(query, titre) >= VARIANTE_MIN:
            rivaux.append(item)
    return rivaux


@dataclass
class Doubt:
    """Une association que le script refuse de trancher seul.

    Le film n'est volontairement pas charge : c'est la reponse donnee a la fin
    du passage qui dira quelle fiche aller chercher.
    """
    query: str
    candidates: list = field(default_factory=list)   # la retenue d'abord
    notes: list = field(default_factory=list)        # ce qui a mis la puce a l'oreille
    order: object = None                             # ordre de saga (1, ou "1.5")


def choice_list(results, rivals, limit=VARIANTE_MAX):
    """Candidats a proposer : le retenu, puis les concurrents, puis le reste."""
    ordonnes = [results[0]] + list(rivals)
    for item in results[1:]:
        if item not in ordonnes:
            ordonnes.append(item)
    return ordonnes[:limit]


def find_movie(tmdb, rawname, contexts=()):
    """(fiche TMDB, ordre de saga) pour un nom de dossier ou de fichier.

    Meme priorite que pour les series : identifiant epingle dans le nom d'abord,
    recherche sur le titre et l'annee ensuite. Affiche ce qui a ete retenu et les
    doutes. Rend (None, ordre) si rien ne correspond.

    C'est un resultat de RECHERCHE, sans les credits ni les genres : de quoi
    nommer un dossier. Qui veut les details fait ensuite tmdb.movie(id).
    """
    pinned, nom = naming.extract_tmdb_id(rawname)
    title, year, order = naming.parse_title_year(nom)
    if pinned:
        try:
            film = tmdb.movie(pinned)
        except TmdbError as e:
            print(f"  id epingle {pinned} inutilisable : {e}")
            return None, order
        print(f"  id epingle dans le nom : {describe(film)}")
        return film, order

    try:
        results, query = search_with_context(tmdb, title, year, contexts)
    except TmdbError as e:
        print(f"  echec recherche TMDB : {e}")
        return None, order
    if not results:
        print(f"  [NON ASSOCIE] recherche '{title}'"
              + (f" ({year})" if year else "") + " -> aucun resultat")
        return None, order

    best, notes = pick_result(results, query)
    print(f"  recherche : '{query}'" + (f" ({year})" if year else "")
          + (f" [ordre {order}]" if order else "")
          + f" -> {describe(best)}")
    for note in notes:
        print(f"  /!\\ {note}")
    return best, order


def series_query(directory):
    """(titre, annee) a chercher sur TMDB pour un dossier de serie.

    Si --dir pointe sur un dossier de saison, c'est le dossier parent qui porte
    le nom de la serie.
    """
    path = Path(directory).resolve()
    if naming.season_number(path.name) is not None:
        path = path.parent
    _, nom = naming.extract_tmdb_id(path.name)
    title, year, _ = naming.parse_title_year(nom)
    return title, year


def pinned_show_id(directory):
    """Id TMDB epingle dans le nom du dossier de la serie, ou None."""
    path = Path(directory).resolve()
    if naming.season_number(path.name) is not None:
        path = path.parent
    return naming.extract_tmdb_id(path.name)[0]


def resolve_show_id(tmdb, directory, forced=None):
    """Id TMDB de la serie : celui force par --tmdb-id, sinon une recherche sur
    le nom du dossier. Retourne None (en expliquant) si rien ne correspond."""
    if forced:
        return forced
    pinned = pinned_show_id(directory)
    if pinned:
        print(f"id epingle dans le nom du dossier : {pinned}")
        return pinned
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
