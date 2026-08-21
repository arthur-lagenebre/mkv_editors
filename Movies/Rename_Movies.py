#!/usr/bin/env python3
r"""
Rename_Movies.py — Renomme les dossiers (ou fichiers) de films avec les noms TMDB.

Format applique :  "Titre (Annee)"
  - Un prefixe d'ordre de saga est conserve : "1 - Iron Man" -> "1 - Iron Man (2008)".
    C'est lui qui devient le numero dans la collection quand Metadata.py etiquette.
  - Un sous-dossier par film -> c'est le DOSSIER qui est renomme (son contenu suit).
    Des .mkv a plat -> ce sont les FICHIERS, avec leurs sous-titres.
  - --pin-id ecrit l'identifiant dans le nom ("Dune (2021) [tmdbid-438631]") :
    les passages suivants n'ont plus rien a chercher, donc plus rien a se tromper.
  - N'a besoin d'AUCUN outil externe (ni MKVToolNix ni FFmpeg). Juste Internet.

C'est le pendant de TV_Shows/Rename_Episodes.py, et le meilleur moyen de fiabiliser
Metadata.py : toute la reconnaissance des films repose sur le nom du dossier.

Cle TMDB : ligne TMDB_KEY=... du fichier .env, a la racine du depot.

Usage :
  python Rename_Movies.py --dir "D:\Films"                    # simulation
  python Rename_Movies.py --dir "D:\Films" --apply            # renomme
  python Rename_Movies.py --dir "D:\Films" --apply --pin-id   # + epingle l'id TMDB
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pour importer mkvlib
from mkvlib import cache, cli, lookup, naming, rename             # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError                       # noqa: E402


def target_stem(film, order, pin_id):
    """Nom vise pour un film : "Titre (Annee)", avec ce qui doit y survivre.

    L'ordre de saga est conserve parce qu'il porte une information que TMDB n'a
    pas : celle que TU as choisie pour regarder la serie de films.
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
    """Faut-il un identifiant dans le nom ? Oui si on le demande, oui s'il y est deja.

    Sans cette seconde regle, un passage sans --pin-id RETIRERAIT les identifiants
    epingles au passage precedent - exactement ce qu'ils sont la pour eviter.
    """
    return pin_id or naming.extract_tmdb_id(rawname)[0] is not None


def plan_entry(entry, film, order, foldered, pin_id):
    """[(source, cible), ...] pour un film : son dossier, ou ses fichiers a plat."""
    stem = target_stem(film, order, wants_pin(entry.rawname, pin_id))
    if foldered:
        return [(entry.folder, entry.folder.with_name(stem))]

    renames = []
    for video in entry.files:
        renames.append((video, video.with_name(stem + video.suffix.lower())))
        # A plat, les sous-titres poses a cote doivent suivre leur video.
        renames += rename.sidecar_renames(video, stem)
    return renames


def plan_library(movies, foldered, args, tmdb):
    """(planned, tally) pour toute la mediatheque, affichage compris."""
    planned, claimed, tally = [], {}, rename.Tally(total=len(movies))
    for entry in movies:
        print(f"--- {entry.rawname} ---")
        film, order = lookup.find_movie(tmdb, entry.rawname)
        if film is None:
            print()
            continue

        mouvements = [(src, dst) for src, dst in plan_entry(entry, film, order, foldered, args.pin_id)
                      if dst != src]
        cible = os.path.normcase(target_stem(film, order, wants_pin(entry.rawname, args.pin_id)))
        if not mouvements:
            # Celui qui porte deja le nom en est le proprietaire, quel que
            # soit l'ordre de parcours : c'est un autre qui devra ceder.
            claimed[cible] = entry.rawname
            tally.named += 1
            print("  deja au bon nom\n")
            continue
        jumeau = claimed.setdefault(cible, entry.rawname)
        if jumeau != entry.rawname:
            print(f"  [DOUBLON] vise le meme nom que '{jumeau}' -> ignore\n")
            continue
        for src, dst in mouvements:
            print(f"  {src.name}\n       -> {dst.name}")
        planned += mouvements
        print()
    return planned, tally


def rename_library(movies, foldered, args, tmdb):
    """Affiche le plan et l'applique si --apply. Retourne le Tally."""
    planned, tally = plan_library(movies, foldered, args, tmdb)
    if args.apply:
        renommes = rename.apply_renames(planned)
        # Les dossiers (ou les videos) comptent comme des films ; le reste, ce
        # sont les sous-titres qui les ont suivis.
        principaux = ({e.folder for e in movies} if foldered
                      else {f for e in movies for f in e.files})
        tally.named += len(renommes & principaux)
        tally.subtitles = len(renommes - principaux)
    return tally


def main():
    ap = argparse.ArgumentParser(
        description="Renomme les dossiers de films au format 'Titre (Annee)' (donnees TMDB).")
    ap.add_argument("--dir", required=True,
                    help="Dossier de films (un sous-dossier par film, ou des .mkv a plat)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--apply", action="store_true", help="Renomme reellement (defaut : simulation)")
    ap.add_argument("--pin-id", action="store_true",
                    help="Ajoute l'identifiant TMDB au nom ('[tmdbid-438631]')")
    ap.add_argument("--no-cache", action="store_true",
                    help="Ignore le cache des reponses TMDB et le rafraichit")
    args = ap.parse_args()

    cli.setup_console()
    cli.check_dir(args.dir)
    tmdb = Tmdb(cli.resolve_tmdb_key(), args.language, user_agent="rename_movies/1.0",
                cache=cache.Cache(read=not args.no_cache))

    mode = cli.mode_label(args, "rien ne sera renomme ; ajoute --apply")
    print(f"=== {mode} ===   source : TMDB {args.language}\n")

    movies, foldered = naming.find_movies(args.dir)
    if not movies:
        print(f"Aucun .mkv trouve dans : {args.dir}")
        return 0

    quoi = "dossier(s)" if foldered else "fichier(s)"
    tally = rename_library(movies, foldered, args, tmdb)
    sous_titres = (f", {tally.subtitles} sous-titre(s) renomme(s)") if tally.subtitles else ""
    print(f"TOTAL : {tally.named}/{tally.total} {quoi} au bon nom{sous_titres}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
