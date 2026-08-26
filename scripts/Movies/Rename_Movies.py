#!/usr/bin/env python3
r"""
Rename_Movies.py — Renomme les dossiers (ou fichiers) de films avec les noms TMDB.

Format applique :  "Titre (Année)"
  - Un préfixe d'ordre de saga est conservé : "1 - Iron Man" -> "1 - Iron Man (2008)". C'est lui qui devient le numéro dans la collection quand Metadata.py étiquette.
  - Un dossier qui ne contient qu'un film -> c'est le DOSSIER qui est renommé (son contenu suit). Partout ailleurs -> les FICHIERS, avec leurs sous-titres. --dir est parcouru récursivement : un dossier de saga garde son nom, et les films qu'il contient sont renommés un par un.
  - --pin-id écrit l'identifiant dans le nom ("Dune (2021) [tmdbid-438631]") : les passages suivants n'ont plus rien à chercher, donc plus rien à se tromper.
  - N'a besoin d'AUCUN outil externe (ni MKVToolNix ni FFmpeg). Juste Internet.

C'est le pendant de TV_Shows/Rename_Episodes.py, et le meilleur moyen de fiabiliser Metadata.py : toute la reconnaissance des films repose sur leur nom.

Clé TMDB : ligne TMDB_KEY=... du fichier .env, à la racine du dépôt.

Usage :
  python Rename_Movies.py --dir "D:\Films"                    # simulation
  python Rename_Movies.py --dir "D:\Films" --apply            # renomme
  python Rename_Movies.py --dir "D:\Films" --apply --pin-id   # + épinglé l'id TMDB
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # pour importer mkvlib
from mkvlib import cache, cli, lookup, naming, rename             # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError                       # noqa: E402


def target_stem(film, order, pin_id):
    """Nom vise pour un film : "Titre (Année)", avec ce qui doit y survivre.

    L'ordre de saga est conservé parce qu'il porte une information que TMDB n'a pas : celle que TU as choisie pour regarder la série de films.
    """
    titre = naming.safe_name(film.get("title", ""))
    annee = (film.get("release_date") or "")[:4]
    stem = f"{titre} ({annee})" if annee else titre
    if order:
        stem = f"{order} - {stem}"
    if pin_id and film.get("id"):
        stem += f" [tmdbid-{film['id']}]"
    return stem


def wants_pin(rawname, pin_id):
    """Faut-il un identifiant dans le nom ? Oui si on le demande, oui s'il y est déjà.

    Sans cette seconde règle, un passage sans --pin-id RETIRERAIT les identifiants épinglés au passage précédent - exactement ce qu'ils sont la pour éviter.
    """
    return pin_id or naming.extract_tmdb_id(rawname)[0] is not None


def plan_entry(entry, film, order, pin_id):
    """[(source, cible), ...] pour un film : son dossier, ou son fichier.

    Le dossier n'est renommé que s'il ne contient que ce film : dans un dossier de saga, c'est chaque fichier qui prend le nom TMDB, et le dossier ne bouge pas.
    """
    stem = target_stem(film, order, wants_pin(entry.rawname, pin_id))
    if entry.owns_folder:
        return [(entry.folder, entry.folder.with_name(stem))]

    renames = []
    for video in entry.files:
        renames.append((video, video.with_name(stem + video.suffix.lower())))
        # Les sous-titres posés à côté doivent suivre leur vidéo.
        renames += rename.sidecar_renames(video, stem)
    return renames


def plan_library(movies, args, tmdb):
    """(planned, tally) pour toute la médiathèque, affichage compris."""
    planned, claimed, tally = [], {}, rename.Tally(total=len(movies))
    for entry in movies:
        print(f"--- {entry.display} ---")
        film, order = lookup.find_movie(tmdb, entry.rawname, entry.contexts)
        if film is None:
            print()
            continue

        mouvements = [(src, dst) for src, dst in plan_entry(entry, film, order, args.pin_id) if dst != src]
        cible = os.path.normcase(target_stem(film, order, wants_pin(entry.rawname, args.pin_id)))
        if not mouvements:
            # Celui qui porte déjà le nom en est le propriétaire, quel que soit l'ordre de parcours : c'est un autre qui devra céder.
            claimed[cible] = entry.display
            tally.named += 1
            print("  deja au bon nom\n")
            continue
        jumeau = claimed.setdefault(cible, entry.display)
        if jumeau != entry.display:
            print(f"  [DOUBLON] vise le meme nom que '{jumeau}' -> ignore\n")
            continue
        for src, dst in mouvements:
            print(f"  {src.name}\n       -> {dst.name}")
        planned += mouvements
        print()
    return planned, tally


def rename_library(movies, args, tmdb):
    """Affiche le plan et l'applique si --apply. Retourne le Tally."""
    planned, tally = plan_library(movies, args, tmdb)
    if args.apply:
        renommes = rename.apply_renames(planned)
        # Le dossier (ou la vidéo) compte comme le film ; le reste, ce sont les sous-titres qui l'ont suivi.
        principaux = {e.folder if e.owns_folder else f for e in movies for f in e.files}
        tally.named += len(renommes & principaux)
        tally.subtitles = len(renommes - principaux)
    return tally


def main():
    ap = argparse.ArgumentParser(description="Renomme les dossiers de films au format 'Titre (Annee)' (donnees TMDB).")
    ap.add_argument("--dir", required=True, help="Dossier de films, parcouru recursivement (dossiers de saga compris)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--apply", action="store_true", help="Renomme reellement (defaut : simulation)")
    ap.add_argument("--pin-id", action="store_true", help="Ajoute l'identifiant TMDB au nom ('[tmdbid-438631]')")
    ap.add_argument("--no-cache", action="store_true", help="Ignore le cache des reponses TMDB et le rafraichit")
    args = ap.parse_args()

    cli.setup_console()
    cli.check_dir(args.dir)
    tmdb = Tmdb(cli.resolve_tmdb_key(), args.language, user_agent="rename_movies/1.0", cache=cache.Cache(read=not args.no_cache))

    mode = cli.mode_label(args, "rien ne sera renomme ; ajoute --apply")
    print(f"=== {mode} ===   source : TMDB {args.language}\n")

    movies = naming.find_movies(args.dir)
    if not movies:
        print(f"Aucun .mkv trouve dans : {args.dir}")
        return 0

    tally = rename_library(movies, args, tmdb)
    sous_titres = (f", {tally.subtitles} sous-titre(s) renomme(s)") if tally.subtitles else ""
    print(f"TOTAL : {tally.named}/{tally.total} film(s) au bon nom{sous_titres}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
