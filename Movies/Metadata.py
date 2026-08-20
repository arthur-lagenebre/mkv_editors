#!/usr/bin/env python3
r"""
Metadata.py — Etiquette des films .mkv a partir de TMDB (donnees en francais via l'API).

Meme principe que TV_Shows/Metadata.py, mais pour les films : pas de saisons/episodes,
et l'association se fait par RECHERCHE TMDB sur le titre + l'annee extraits du nom.

Ecrit DIRECTEMENT dans chaque .mkv (sans re-encodage ni remux) :
  - le titre et la DATE de sortie dans les informations de segment
    (sortie du pays de --language : fr-FR -> sortie francaise, pas la sortie d'origine)
  - le synopsis, le realisateur, les scenaristes, le casting, les genres (tags)
  - les tags de STATISTIQUES de piste (debit, duree, nb d'images)  [--no-stats]
  - l'affiche du film comme jaquette (attachment "cover.jpg")
  - le nom des pistes AUDIO       -> codec + canaux + debit (ex. "E-AC-3 5.1 640 kb/s")
  - le nom des pistes SOUS-TITRES -> uniquement les drapeaux actifs (Forced, SDH...), ou "Full"
  - les DRAPEAUX 'par defaut'     -> une seule piste audio par defaut (la FR), aucun sous-titre

Dependances EXTERNES (dans le PATH) : mkvpropedit + mkvmerge (MKVToolNix), ffprobe (FFmpeg).
Aucune dependance pip. Necessite Internet (API TMDB + jaquettes).
Le code partage avec les autres scripts du depot vit dans mkvlib/ (a la racine).

Cle TMDB (par priorite) : fichier .env a la racine du depot (TMDB_KEY=...)  >  variable
d'env TMDB_API_KEY  >  constante TMDB_KEY.
(Le meme .env sert a tous les scripts du depot : la cle n'est ecrite qu'une fois.)

Structure attendue : soit un sous-dossier par film (les .mkv dedans), soit des .mkv a plat
dans --dir. Le titre et l'annee sont lus dans le nom (dossier ou fichier), ex. "Inception (2010)".
Un prefixe d'ordre de saga "{n} - " est detecte et retire pour la recherche ("1 - Iron Man"
-> recherche "Iron Man") ; l'ordre est inscrit comme numero dans la collection (tag PART_NUMBER).

Usage :
  python Metadata.py --dir "D:\Films"                         # simulation (n'ecrit rien)
  python Metadata.py --dir "D:\Films" --apply                 # applique
  python Metadata.py --dir "D:\Films\Inception (2010)" --tmdb-id 27205 --apply   # force l'id (1 film)
  python Metadata.py --dir "D:\Films" --verify                # verifie seulement

Options : --apply --verify --skip-done --artwork
          --no-cover --no-date --no-audio-names --no-sub-names --no-flags --no-stats
          --tmdb-id (force, si un seul film) --language (defaut fr-FR) --image-size (w780)
"""

import argparse
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pour importer mkvlib
from mkvlib import artwork, cli, mkv, naming                      # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError, release_region   # noqa: E402

# ============================================================================
# Cle API TMDB : colle-la ici entre les guillemets pour ne plus avoir a la
# retaper. Priorite : .env > env TMDB_API_KEY > ceci.
TMDB_KEY = ""
# ============================================================================


# ----------------------------------------------------------------------------
# 1. Detection des films sur le disque
# ----------------------------------------------------------------------------
def find_movies(root):
    """(films, foldered). films = [(dossier, mkv_principal, nom_brut), ...].
    - Sous-dossiers contenant des .mkv -> un film par dossier (nom = dossier).
    - Sinon, chaque .mkv de --dir -> un film (nom = fichier)."""
    root = Path(root)
    if not root.is_dir():
        return [], False
    foldered = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        mkvs = sorted(sub.glob("*.mkv"))
        if mkvs:
            main = max(mkvs, key=lambda f: f.stat().st_size)   # le plus gros = le film
            foldered.append((sub, main, sub.name))
    if foldered:
        return foldered, True
    return [(root, f, f.stem) for f in sorted(root.glob("*.mkv"))], False


# ----------------------------------------------------------------------------
# 2. Choix du film sur TMDB
# ----------------------------------------------------------------------------
def _norm(s):
    return " ".join((s or "").lower().split())


def pick_result(results, query):
    """(film, remarques). Le premier resultat TMDB, avec les doutes rendus visibles.

    TMDB classe par popularite : quand deux films portent le meme titre (remake) ou
    quand le titre trouve n'a plus grand-chose a voir avec la recherche, le premier
    resultat n'est pas forcement le bon. Autant le dire que de l'ecrire en silence.
    """
    best = results[0]
    notes = []
    if len(results) > 1 and _norm(results[1].get("title")) == _norm(best.get("title")):
        other = results[1]
        notes.append(f"titre partage avec {other.get('title')} "
                     f"({(other.get('release_date') or '?')[:4]}) [id {other.get('id')}] "
                     "-> verifie l'annee, ou force --tmdb-id")
    if SequenceMatcher(None, _norm(query), _norm(best.get("title"))).ratio() < 0.6:
        notes.append("titre trouve eloigne de la recherche -> a verifier")
    return best, notes


# ----------------------------------------------------------------------------
# 3. Etat vise pour un film (TargetTypeValue 50 = film, 70 = collection/saga)
# ----------------------------------------------------------------------------
def build_movie_tags_xml(movie, max_actors=20):
    credits = movie.get("credits", {})
    blocks = []

    collection = (movie.get("belongs_to_collection") or {}).get("name")
    if collection:
        lines = [mkv.simple("TITLE", collection)]
        if movie.get("_order"):                       # ordre de la saga (prefixe "{n} - ")
            lines.append(mkv.simple("PART_NUMBER", movie["_order"]))
        blocks.append(mkv.tag_block(70, lines))

    lines = [mkv.simple("TITLE", movie.get("title", ""))]
    if movie.get("overview"):
        lines.append(mkv.simple("SYNOPSIS", movie["overview"]))
        lines.append(mkv.simple("SUMMARY", movie["overview"]))
    if movie.get("release_date"):
        lines.append(mkv.simple("DATE_RELEASED", movie["release_date"]))
    lines += mkv.credits_lines(credits.get("crew", []), credits.get("cast", []), max_actors)
    genres = ", ".join(g.get("name", "") for g in movie.get("genres", []))
    if genres:
        lines.append(mkv.simple("GENRE", genres))
    blocks.append(mkv.tag_block(50, lines))
    return mkv.tags_document(blocks)


def movie_target(movie, opts):
    """Ce que le .mkv de ce film devrait contenir."""
    return mkv.Target(
        title=movie.get("title", ""),
        date=movie.get("release_date"),
        tags_xml=build_movie_tags_xml(movie),
        poster=movie.get("poster_path") if opts.cover else None,
    )


# ----------------------------------------------------------------------------
# 4. Traitement d'un film
# ----------------------------------------------------------------------------
def process_movie(folder, path, movie, args, opts, tmdb, foldered):
    info = mkv.identify(path)
    if info and args.probe:
        mkv.annotate_bitrates(info, path)      # debits pour le nom des pistes
    target = movie_target(movie, opts)

    if args.verify:
        diffs = [(lbl, det) for lbl, ok, det in mkv.verify(info, target, opts) if not ok]
        for lbl, det in diffs:
            print(f"      [DIFF] {lbl} : actuel = {det!r}")
        if not diffs:
            print("      [OK] deja conforme")
        return

    if opts.date and target.date:
        origine = (f" (sortie {movie['_date_region']})" if movie.get("_date_region")
                   else " (sortie d'origine)")
        print(f"      date -> {target.date}{origine}")
    for line in mkv.track_preview_lines(info, opts):
        print(line)

    if args.apply:
        if args.skip_done and mkv.is_conform(info, target, opts):
            print("      [SKIP] deja a jour")
        else:
            code, msg = mkv.write(path, info, target, opts, tmdb)
            print(f"      [{'OK' if code == 0 else 'ECHEC'}]" + (f" {msg}" if code else ""))

    if args.artwork and foldered:
        poster = artwork.english_poster(
            lambda: tmdb.movie(movie["id"], artwork.ARTWORK_LANG),
            movie.get("poster_path"))
        print(f"      affiche (EN) : {artwork.write_poster(poster, folder, args.apply, tmdb)}")


def resolve_movie(rawname, args, tmdb, single):
    """Trouve le film TMDB correspondant a un nom de dossier/fichier, ou None."""
    title, year, order = naming.parse_title_year(rawname)
    if args.tmdb_id and single:
        movie_id = args.tmdb_id
    else:
        try:
            results = tmdb.search_movie(title, year)
            if not results and year:
                results = tmdb.search_movie(title, None)
        except TmdbError as e:
            print(f"  echec recherche TMDB : {e}\n")
            return None
        if not results:
            print(f"  [NON ASSOCIE] recherche '{title}'"
                  + (f" ({year})" if year else "") + " -> aucun resultat\n")
            return None
        best, notes = pick_result(results, title)
        movie_id = best["id"]
        print(f"  recherche : '{title}'" + (f" ({year})" if year else "")
              + (f" [ordre {order}]" if order else "")
              + f" -> {best.get('title')} ({(best.get('release_date') or '?')[:4]}) [id {movie_id}]")
        for note in notes:
            print(f"  /!\\ {note}")

    try:
        movie = tmdb.movie(movie_id)
    except TmdbError as e:
        print(f"  echec details TMDB : {e}\n")
        return None
    movie["_order"] = order

    # Sortie nationale (fr-FR -> FR) : sans ca, TMDB donne la sortie d'origine.
    region = release_region(args.language)
    local = (tmdb.local_release_date(movie_id, region)
             if region and not args.no_date else None)
    if local:
        movie["release_date"], movie["_date_region"] = local, region
    return movie


# ----------------------------------------------------------------------------
# 5. Programme principal
# ----------------------------------------------------------------------------
def parse_args():
    ap = argparse.ArgumentParser(description="Etiquette des films .mkv depuis TMDB (en francais).")
    ap.add_argument("--dir", required=True,
                    help="Dossier de films (un sous-dossier par film, ou des .mkv a plat)")
    ap.add_argument("--tmdb-id", help="Force l'id TMDB (utile si --dir ne contient qu'un seul film)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--apply", action="store_true", help="Applique reellement (defaut : simulation)")
    ap.add_argument("--verify", action="store_true", help="Verifie seulement (aucune ecriture)")
    ap.add_argument("--skip-done", action="store_true", help="Saute les films deja conformes")
    ap.add_argument("--no-cover", action="store_true", help="N'embarque pas la jaquette")
    ap.add_argument("--no-date", action="store_true", help="Ne modifie pas la date du segment")
    ap.add_argument("--no-audio-names", action="store_true", help="Ne renomme pas les pistes audio")
    ap.add_argument("--no-sub-names", action="store_true", help="Ne renomme pas les pistes de sous-titres")
    ap.add_argument("--no-flags", action="store_true", help="Ne touche pas aux drapeaux 'par defaut'")
    ap.add_argument("--no-stats", action="store_true", help="N'ajoute pas les tags de statistiques")
    ap.add_argument("--artwork", action="store_true", help="Ecrit folder.jpg (affiche EN) par film")
    ap.add_argument("--image-size", default="w780", help="Taille TMDB : w300 / w780 / original")
    return ap.parse_args()


def main():
    args = parse_args()
    cli.setup_console()
    args.probe = mkv.check_tools()
    opts = mkv.Options.from_args(args)
    tmdb = Tmdb(cli.resolve_tmdb_key(TMDB_KEY), args.language, user_agent="movies_mkv/1.0")

    print(f"=== {cli.mode_label(args)} ===   source : TMDB {args.language}\n")

    movies, foldered = find_movies(args.dir)
    if not movies:
        print(f"Aucun .mkv trouve dans : {args.dir}")
        return
    if args.tmdb_id and len(movies) > 1:
        print(f"Note : --tmdb-id ne s'applique qu'a un seul film ; {len(movies)} detectes "
              "-> id ignore, recherche par nom.\n")
    if args.artwork and not foldered:
        print("Note : --artwork sans effet ici (les .mkv sont a plat, pas un dossier par film).\n")

    matched = 0
    for folder, path, rawname in movies:
        print(f"--- {path.name} ---")
        movie = resolve_movie(rawname, args, tmdb, single=len(movies) == 1)
        if movie is None:
            continue
        matched += 1
        process_movie(folder, path, movie, args, opts, tmdb, foldered)
        print()

    print(f"TOTAL : {matched}/{len(movies)} film(s) associe(s).")


if __name__ == "__main__":
    try:
        main()
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
