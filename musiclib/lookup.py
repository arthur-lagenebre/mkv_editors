"""Association d'un album du disque à une sortie MusicBrainz.

Étiqueter un album et renommer ses pistes posent la même question - "quelle édition de quel album ce dossier contient-il ?" - et méritent la même réponse : l'identifiant déjà connu d'abord, la recherche ensuite, et les hésitations mises de côté pour la fin du passage plutôt que tranchées en silence.

Contrairement à album.py, ce module parle au réseau et à l'utilisateur : il affiche ce qu'il retient.
"""

from dataclasses import dataclass, field

from mkvlib import cli

from . import album as albums
from . import flac
from .musicbrainz import MusicBrainzError


@dataclass
class Found:
    """Un album du disque, ses fichiers lus, et ce qu'on sait de lui."""
    album: albums.Album
    metas: dict                                   # {chemin: flac.Metadata}
    hints: albums.Hints = field(default_factory=albums.Hints)
    choice: albums.Choice | None = None           # quand une question reste à poser


def read_album(album):
    """({chemin: Metadata}, [erreurs]). Un fichier illisible bloque l'album entier."""
    metas, errors = {}, []
    for path in album.files:
        try:
            metas[path] = flac.read(path)
        except flac.FlacError as e:
            errors.append(f"{path.name} : {e}")
    return metas, errors


def search_queries(album, metas):
    """[(titre, artiste)] à essayer, les tags d'abord, le nom du dossier ensuite - sans doublon."""
    tags, folder = albums.tag_hints(list(metas.values())), albums.folder_hints(album.folder)
    queries = []
    for title, artist in ((tags.title, tags.artist or folder.artist), (folder.title, folder.artist)):
        if title and (title, artist) not in queries:
            queries.append((title, artist))
    return queries


def fetch_release(mbid, mb, with_genres=True):
    """(sortie complète, release group). Le release group n'est redemandé que pour ses genres : sans eux, celui que la sortie embarque suffit."""
    release = mb.release(mbid)
    group = release.get("release-group") or {}
    if with_genres and group.get("id"):
        try:
            group = mb.release_group(group["id"])
        except MusicBrainzError as e:
            print(f"  genres indisponibles : {e}")
    return release, group


def resolve(found, mb, forced=None, country="FR", ask=True, with_genres=True):
    """(sortie, release group) pour un album, ou (None, None) - avec found.choice rempli si une question reste à poser.

    Priorité : `forced` (--mbid), l'identifiant épinglé dans le nom du dossier, celui que TOUS les fichiers déclarent, puis la recherche. Sans `ask`, deux albums du même titre qui tiennent ne posent pas de question : le premier est gardé.
    """
    album, metas = found.album, found.metas
    folder, tags = albums.folder_hints(album.folder), albums.tag_hints(list(metas.values()))
    found.hints = albums.Hints(tags.artist or folder.artist, tags.title or folder.title, folder.year or tags.year)

    known = [(forced, "id force par --mbid"), (folder.pinned, "id epingle dans le nom"), (tags.tagged, "id lu dans les fichiers")]
    mbid, origin = next(((i, o) for i, o in known if i), (None, ""))
    if mbid:
        release, group = fetch_release(mbid, mb, with_genres)
        print(f"  {origin} : {albums.describe(release)}")
        return release, group

    choice = albums.Choice(reason="nom de dossier et tags inexploitables")
    for title, artist in search_queries(album, metas):
        hints = albums.Hints(artist, title, found.hints.year)
        results = mb.search_releases(title, artist) or mb.search_releases(title, artist, exact=False)
        choice = albums.choose_release(results, len(album.files), hints, country, album.disc_count)
        if choice.release:
            print(f"  recherche : '{title}' / '{artist}'" + (f" ({hints.year})" if hints.year else "") + f" -> {albums.describe(choice.release)}")
            break
    if choice.release is None:
        print(f"  [NON ASSOCIE] {choice.reason}")
        return None, None
    if choice.rivals and ask:
        found.choice = choice
        print(f"  [A CONFIRMER] {len(choice.rivals) + 1} albums de ce titre tiennent -> question en fin de passage")
        return None, None
    return fetch_release(choice.release["id"], mb, with_genres)


def confirm(pending):
    """Pose les questions mises de côté : rend (found, sortie choisie) pour chaque album, ou (found, None) s'il reste sans réponse.

    Comme pour les films, les questions attendent la fin : le script déroule d'abord tout ce qu'il sait faire seul, et n'arbitre qu'ensuite. C'est un générateur, pour que l'album confirmé soit traité avant que la question suivante ne s'affiche.
    """
    print(f"=== {len(pending)} album(s) a confirmer ===")
    if not cli.can_ask():
        print("  Terminal non interactif : ces albums sont laisses de cote.")
        print("  Relance dans un terminal, epingle l'id dans le nom du dossier, ou passe")
        print("  --no-ask pour accepter le premier resultat sans demander.\n")
        for found in pending:
            print(f"  [A CONFIRMER] {found.album.display} -> {albums.describe(found.choice.release)}")
            yield found, None
        return

    print("  Entree = garder le 1er, i = laisser de cote, q = arreter les questions.\n")
    stopped = False
    for number, found in enumerate(pending, 1):
        candidates = [found.choice.release] + found.choice.rivals
        print(f"[{number}/{len(pending)}] {found.album.display}   (recherche : '{found.hints.title}')")
        for rank, candidate in enumerate(candidates, 1):
            print(f"      {rank}) {albums.describe(candidate)}" + ("   (defaut)" if rank == 1 else ""))
        answer = cli.ASK_SKIP if stopped else cli.ask_choice(len(candidates))
        if answer == cli.ASK_STOP:
            stopped, answer = True, cli.ASK_SKIP
        if answer == cli.ASK_SKIP:
            print("      [NON TRAITE] album non confirme\n")
            yield found, None
            continue
        chosen = candidates[answer]
        print(f"      pour ne plus avoir la question : ajoute ' [mbid-{chosen['id']}]' au nom du dossier")
        yield found, chosen
