#!/usr/bin/env python3
r"""
Rename_Episodes.py — Renomme les épisodes d'une série avec les noms TMDB (en français).

Format applique :  "{numéro} - {nom de l'épisode}.ext"
  - Le numéro est zero-padde pour avoir le MÊME nombre de digits dans toute la saison (largeur = nb de digits du plus grand numéro, minimum 2).  ex : 01, 02, ... 15
  - N'importe quel format vidéo (mkv, mp4, avi, m4v, mov, ts...) : ne touche qu'au NOM.
  - Les SOUS-TITRES posés à côté suivent leur vidéo (.srt, .ass, .idx/.sub...), en conservant ce qui suit le nom : "S01E02.fr.forced.srt" -> "02 - Titre.fr.forced.srt".
  - N'a besoin d'AUCUN outil externe (ni MKVToolNix ni FFmpeg). Juste Internet pour TMDB.

L'association fichier <-> épisode se fait par le numéro présent dans le nom actuel (S01E05, 1x05, 05 - ..., Épisode 5...), avec repli sur une correspondance de titre.

Clé TMDB : ligne TMDB_KEY=... du fichier .env, à la racine du dépôt.

Structure : un sous-dossier "Saison N" par saison, ou --dir pointant sur un dossier de saison.

Usage :
  python Rename_Episodes.py --dir "D:\Séries\Ma Série"                  # simulation
  python Rename_Episodes.py --dir "D:\Séries\Ma Série" --apply          # renomme
  python Rename_Episodes.py --dir "D:\Séries\Ma Série" --tmdb-id 1234   # id force

La série est identifiée par une recherche TMDB sur le nom du dossier ; --tmdb-id n'est utile que si la recherche se trompe ou ne trouve rien.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # pour importer mkvlib
from mkvlib import cache, cli, lookup, naming, rename             # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError            # noqa: E402


def plan_season(folder, season, threshold):
    """Prévoit les renommages d'un dossier. Retourne (planned, tally).

    `planned` = [(source, destination), ...], vidéos et sous-titres mélanges. Deux fichiers qui visent le même nom (deux versions du même épisode, par exemple) sont signalés ici : au moment d'écrire, le second échouerait sans explication.
    """
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    if not by_num:
        print("  aucune donnee d'episode TMDB pour cette saison")
        return [], rename.Tally()

    width = max(2, len(str(max(by_num))))   # même nb de digits pour toute la saison
    files = naming.files_with_ext(folder, naming.VIDEO_EXTS)
    if not files:
        print("  aucun fichier video")
        return [], rename.Tally()

    planned, claimed, tally = [], {}, rename.Tally(total=len(files))
    for f in files:
        ep, _ = naming.match_episode(f.name, episodes, threshold, by_num)
        if ep is None:
            print(f"  [NON ASSOCIE] {f.name}")
            continue

        n = ep.get("episode_number", 0)
        stem = f"{n:0{width}d} - {naming.safe_name(ep.get('name', ''))}"
        dst = f.with_name(stem + f.suffix.lower())
        key = os.path.normcase(dst.name)
        if key in claimed:
            print(f"  [DOUBLON] {f.name} vise le meme nom que {claimed[key].name} -> ignore")
            continue
        claimed[key] = f

        # Les sous-titres suivent même quand la vidéo, elle, est déjà bien nommée.
        subs = [(src, cible) for src, cible in rename.sidecar_renames(f, stem) if cible != src]
        if dst.name == f.name:
            tally.named += 1
            if subs:
                print(f"  {n:0{width}d} : {f.name}")
        else:
            planned.append((f, dst))
            print(f"  {n:0{width}d} : {f.name}")
            print(f"       -> {dst.name}")
        for src, cible in subs:
            planned.append((src, cible))
            tally.subtitles += 1
            print(f"       + {src.name}  ->  {cible.name}")
    return planned, tally


def rename_season(folder, season, args):
    """Affiche le plan et l'applique si --apply. Retourne le Tally de la saison."""
    planned, tally = plan_season(folder, season, args.match_threshold)
    if args.apply:
        renamed = rename.apply_renames(planned)
        videos = {src for src, _ in planned if src.suffix.lower() in naming.VIDEO_EXTS}
        tally.named += len(videos & renamed)
        tally.subtitles = len(renamed - videos)   # ce qui a vraiment bouge
    return tally


# ----------------------------------------------------------------------------
# Programme principal
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Renomme les episodes d'une serie au format '{numero} - {nom}.ext' (donnees TMDB).")
    ap.add_argument("--dir", required=True, help="Racine de la serie (dossiers 'Saison N') OU un dossier de saison")
    ap.add_argument("--tmdb-id", help="Identifiant TMDB de la serie "
                    "(par defaut : recherche sur le nom du dossier)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--no-cache", action="store_true", help="Ignore le cache des reponses TMDB et le rafraichit")
    ap.add_argument("--apply", action="store_true", help="Renomme reellement (defaut : simulation)")
    ap.add_argument("--match-threshold", type=float, default=0.55, help="Score minimal pour une association par titre (0-1)")
    args = ap.parse_args()

    cli.setup_console()
    cli.check_dir(args.dir)
    tmdb = Tmdb(cli.resolve_tmdb_key(), args.language, user_agent="rename_ep/1.0", cache=cache.Cache(read=not args.no_cache))

    mode = cli.mode_label(args, "rien ne sera renomme ; ajoute --apply")
    print(f"=== {mode} ===   source : TMDB {args.language}")
    args.tmdb_id = lookup.resolve_show_id(tmdb, args.dir, args.tmdb_id)
    if args.tmdb_id is None:
        sys.exit("Serie non identifiee : relance avec --tmdb-id.")
    print()

    seasons = naming.find_seasons(args.dir)
    bilan = rename.Tally()
    if seasons:
        for sub, num in seasons:
            print(f"--- {sub.name}  (TMDB saison {num}) ---")
            try:
                data = tmdb.season(args.tmdb_id, num)
            except TmdbError as e:
                print(f"  echec TMDB saison {num} : {e} -> saison ignoree\n")
                continue
            bilan += rename_season(sub, data, args)
            print()
    else:
        num = naming.season_number(Path(args.dir).name)
        num = 1 if num is None else num      # 0 = les spéciaux, à ne pas confondre
        print(f"--- {Path(args.dir).name}  (TMDB saison {num}) ---")
        try:
            data = tmdb.season(args.tmdb_id, num)
        except TmdbError as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        bilan += rename_season(Path(args.dir), data, args)

    sous_titres = (f", {bilan.subtitles} sous-titre(s) " + ("renomme(s)" if args.apply else "a renommer")) if bilan.subtitles else ""
    print(f"TOTAL : {bilan.named}/{bilan.total} fichier(s) au bon nom{sous_titres}.")


if __name__ == "__main__":
    try:
        main()
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
