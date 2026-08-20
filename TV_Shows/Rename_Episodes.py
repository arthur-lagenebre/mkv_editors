#!/usr/bin/env python3
r"""
Rename_Episodes.py — Renomme les episodes d'une serie avec les noms TMDB (en francais).

Format applique :  "{numero} - {nom de l'episode}.ext"
  - Le numero est zero-padde pour avoir le MEME nombre de digits dans toute la saison
    (largeur = nb de digits du plus grand numero, minimum 2).  ex : 01, 02, ... 15
  - N'importe quel format video (mkv, mp4, avi, m4v, mov, ts...) : ne touche qu'au NOM.
  - N'a besoin d'AUCUN outil externe (ni MKVToolNix ni FFmpeg). Juste Internet pour TMDB.

L'association fichier <-> episode se fait par le numero present dans le nom actuel
(S01E05, 1x05, 05 - ..., Episode 5...), avec repli sur une correspondance de titre.

Cle TMDB (par priorite) : fichier .env a la racine du depot (TMDB_KEY=...) > variable
d'env TMDB_API_KEY > constante TMDB_KEY.

Structure : un sous-dossier "Saison N" par saison, ou --dir pointant sur un dossier de saison.

Usage :
  python Rename_Episodes.py --dir "D:\Series\Ma Serie" --tmdb-id 1234           # simulation
  python Rename_Episodes.py --dir "D:\Series\Ma Serie" --tmdb-id 1234 --apply   # renomme
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pour importer mkvlib
from mkvlib import cli, naming                                    # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError            # noqa: E402

# ============================================================================
# Cle API TMDB (par priorite : .env > env TMDB_API_KEY > ceci).
TMDB_KEY = ""
# ============================================================================

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts",
              ".flv", ".webm", ".mpg", ".mpeg", ".mts", ".vob", ".ogm"}


# ----------------------------------------------------------------------------
# Renommage sur le disque
# ----------------------------------------------------------------------------
def _free_name(path):
    """Nom temporaire libre a cote de `path`, pour un renommage en deux temps."""
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem}.tmp{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("aucun nom temporaire libre")


def _same_file(a, b):
    """Vrai si les deux chemins designent le meme fichier (casse differente incluse)."""
    try:
        return b.exists() and a.samefile(b)
    except OSError:
        return False


def rename_path(src, dst):
    """Renomme src en dst, y compris quand seule la CASSE change.

    Windows considere "titre.mkv" et "Titre.mkv" comme le meme fichier : le
    renommage direct est refuse, il faut passer par un nom intermediaire.
    """
    if _same_file(src, dst):
        tmp = _free_name(src)
        src.rename(tmp)
        tmp.rename(dst)
    else:
        src.rename(dst)


def apply_renames(planned):
    """Applique les renommages prevus. Retourne le nombre de fichiers renommes.

    Un fichier peut viser le nom qu'un autre porte encore (numerotation decalee
    d'un cran) : on repasse alors sur les cas bloques une fois les autres liberes,
    et on casse les cycles restants (01 <-> 02) par un nom temporaire.
    """
    done, pending = 0, list(planned)
    while pending:
        blocked, progress = [], False
        for src, dst in pending:
            if dst.exists() and not _same_file(src, dst):
                blocked.append((src, dst))
                continue
            try:
                rename_path(src, dst)
            except OSError as e:
                print(f"  [ECHEC] {src.name} -> {dst.name} : {e}")
                continue
            done += 1
            progress = True
        if not blocked:
            break
        if progress:                      # des noms se sont liberes : on retente
            pending = blocked
            continue
        cycle = {os.path.normcase(str(s)) for s, _ in blocked}
        stuck = next((pair for pair in blocked
                      if os.path.normcase(str(pair[1])) in cycle), None)
        if stuck is None:                 # vrais conflits : des fichiers etrangers
            for _, dst in blocked:
                print(f"  [IGNORE] existe deja : {dst.name}")
            break
        src, dst = stuck                  # cycle : on degage le premier maillon
        try:
            tmp = _free_name(src)
            src.rename(tmp)
        except OSError as e:
            print(f"  [ECHEC] {src.name} -> {dst.name} : {e}")
            break
        pending = [(tmp if s == src else s, d) for s, d in blocked]
    return done


# ----------------------------------------------------------------------------
# Plan d'une saison
# ----------------------------------------------------------------------------
def plan_season(folder, season, threshold):
    """Prevoit les renommages d'un dossier. Retourne (planned, deja_bons, total).

    `planned` = [(source, destination), ...]. Deux fichiers qui visent le meme
    nom (deux versions du meme episode, par exemple) sont signales ici : au
    moment d'ecrire, le second echouerait sans explication.
    """
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    if not by_num:
        print("  aucune donnee d'episode TMDB pour cette saison")
        return [], 0, 0

    width = max(2, len(str(max(by_num))))   # meme nb de digits pour toute la saison
    files = sorted(f for f in Path(folder).iterdir()
                   if f.is_file() and f.suffix.lower() in VIDEO_EXTS)
    if not files:
        print("  aucun fichier video")
        return [], 0, 0

    planned, claimed, already = [], {}, 0
    for f in files:
        ep = by_num.get(naming.detect_episode_number(f.name))
        if ep is None:
            ep, score = naming.best_title_match(f.name, episodes)
            if score < threshold:
                ep = None
        if ep is None:
            print(f"  [NON ASSOCIE] {f.name}")
            continue

        n = ep.get("episode_number", 0)
        newname = f"{n:0{width}d} - {naming.safe_name(ep.get('name', ''))}{f.suffix.lower()}"
        dst = f.with_name(newname)
        key = os.path.normcase(newname)
        if key in claimed:
            print(f"  [DOUBLON] {f.name} vise le meme nom que {claimed[key].name} -> ignore")
            continue
        claimed[key] = f
        if dst.name == f.name:
            already += 1
            continue
        planned.append((f, dst))
        print(f"  {n:0{width}d} : {f.name}")
        print(f"       -> {newname}")
    return planned, already, len(files)


def rename_season(folder, season, args):
    """Affiche le plan et l'applique si --apply. Retourne (au_bon_nom, total)."""
    planned, already, total = plan_season(folder, season, args.match_threshold)
    if args.apply:
        already += apply_renames(planned)
    return already, total


# ----------------------------------------------------------------------------
# Programme principal
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Renomme les episodes d'une serie au format '{numero} - {nom}.ext' (donnees TMDB).")
    ap.add_argument("--dir", required=True,
                    help="Racine de la serie (dossiers 'Saison N') OU un dossier de saison")
    ap.add_argument("--tmdb-id", required=True, help="Identifiant TMDB de la serie [OBLIGATOIRE]")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--apply", action="store_true", help="Renomme reellement (defaut : simulation)")
    ap.add_argument("--match-threshold", type=float, default=0.55,
                    help="Score minimal pour une association par titre (0-1)")
    args = ap.parse_args()

    cli.setup_console()
    tmdb = Tmdb(cli.resolve_tmdb_key(TMDB_KEY), args.language, user_agent="rename_ep/1.0")

    mode = cli.mode_label(args, "rien ne sera renomme ; ajoute --apply")
    print(f"=== {mode} ===   source : TMDB {args.language}\n")

    seasons = naming.find_seasons(args.dir)
    total_done = total = 0
    if seasons:
        for sub, num in seasons:
            print(f"--- {sub.name}  (TMDB saison {num}) ---")
            try:
                data = tmdb.season(args.tmdb_id, num)
            except TmdbError as e:
                print(f"  echec TMDB saison {num} : {e} -> saison ignoree\n")
                continue
            d, t = rename_season(sub, data, args)
            total_done += d
            total += t
            print()
    else:
        num = naming.season_number(Path(args.dir).name) or 1
        print(f"--- {Path(args.dir).name}  (TMDB saison {num}) ---")
        try:
            data = tmdb.season(args.tmdb_id, num)
        except TmdbError as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        d, t = rename_season(Path(args.dir), data, args)
        total_done += d
        total += t

    print(f"TOTAL : {total_done}/{total} fichier(s) au bon nom.")


if __name__ == "__main__":
    try:
        main()
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
