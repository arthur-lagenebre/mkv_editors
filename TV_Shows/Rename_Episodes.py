#!/usr/bin/env python3
r"""
Rename_Episodes.py — Renomme les episodes d'une serie avec les noms TMDB (en francais).

Format applique :  "{numero} - {nom de l'episode}.ext"
  - Le numero est zero-padde pour avoir le MEME nombre de digits dans toute la saison
    (largeur = nb de digits du plus grand numero, minimum 2).  ex : 01, 02, ... 15
  - N'importe quel format video (mkv, mp4, avi, m4v, mov, ts...) : ne touche qu'au NOM.
  - Les SOUS-TITRES poses a cote suivent leur video (.srt, .ass, .idx/.sub...), en
    conservant ce qui suit le nom : "S01E02.fr.forced.srt" -> "02 - Titre.fr.forced.srt".
  - N'a besoin d'AUCUN outil externe (ni MKVToolNix ni FFmpeg). Juste Internet pour TMDB.

L'association fichier <-> episode se fait par le numero present dans le nom actuel
(S01E05, 1x05, 05 - ..., Episode 5...), avec repli sur une correspondance de titre.

Cle TMDB (par priorite) : fichier .env a la racine du depot (TMDB_KEY=...) > variable
d'env TMDB_API_KEY > constante TMDB_KEY.

Structure : un sous-dossier "Saison N" par saison, ou --dir pointant sur un dossier de saison.

Usage :
  python Rename_Episodes.py --dir "D:\Series\Ma Serie"                  # simulation
  python Rename_Episodes.py --dir "D:\Series\Ma Serie" --apply          # renomme
  python Rename_Episodes.py --dir "D:\Series\Ma Serie" --tmdb-id 1234   # id force

La serie est identifiee par une recherche TMDB sur le nom du dossier ; --tmdb-id
n'est utile que si la recherche se trompe ou ne trouve rien.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # pour importer mkvlib
from mkvlib import cache, cli, lookup, naming                     # noqa: E402
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError            # noqa: E402

# ============================================================================
# Cle API TMDB (par priorite : .env > env TMDB_API_KEY > ceci).
TMDB_KEY = ""
# ============================================================================

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
    """Applique les renommages prevus. Retourne l'ensemble des sources traitees.

    Un fichier peut viser le nom qu'un autre porte encore (numerotation decalee
    d'un cran) : on repasse alors sur les cas bloques une fois les autres liberes,
    et on casse les cycles restants (01 <-> 02) par un nom temporaire. Chaque
    entree garde son chemin d'origine, seul repere stable a travers ces detours.
    """
    done = set()
    pending = [(src, src, dst) for src, dst in planned]   # (origine, source actuelle, cible)
    while pending:
        blocked, progress = [], False
        for origin, src, dst in pending:
            if dst.exists() and not _same_file(src, dst):
                blocked.append((origin, src, dst))
                continue
            try:
                rename_path(src, dst)
            except OSError as e:
                print(f"  [ECHEC] {src.name} -> {dst.name} : {e}")
                continue
            done.add(origin)
            progress = True
        if not blocked:
            break
        if progress:                      # des noms se sont liberes : on retente
            pending = blocked
            continue
        occupees = {os.path.normcase(str(s)) for _, s, _ in blocked}
        bloque = next((t for t in blocked if os.path.normcase(str(t[2])) in occupees), None)
        if bloque is None:                # vrais conflits : des fichiers etrangers
            for _, _, dst in blocked:
                print(f"  [IGNORE] existe deja : {dst.name}")
            break
        _, src, dst = bloque              # cycle : on degage le premier maillon
        try:
            tmp = _free_name(src)
            src.rename(tmp)
        except OSError as e:
            print(f"  [ECHEC] {src.name} -> {dst.name} : {e}")
            break
        pending = [(o, tmp if s == src else s, d) for o, s, d in blocked]
    return done


# ----------------------------------------------------------------------------
# Plan d'une saison
# ----------------------------------------------------------------------------
@dataclass
class Tally:
    """Compte-rendu d'une saison. Additionnable pour totaliser la serie."""
    named: int = 0          # videos deja au bon nom ou renommees
    total: int = 0          # videos vues
    subtitles: int = 0      # sous-titres renommes avec leur video

    def __add__(self, other):
        return Tally(self.named + other.named, self.total + other.total,
                     self.subtitles + other.subtitles)


def sidecar_renames(video, new_stem):
    """[(source, destination), ...] pour les sous-titres poses a cote d'une video.

    Un sous-titre suit sa video s'il porte le meme nom : ce qui vient apres est
    conserve tel quel, pour ne pas perdre la langue ni les drapeaux
    ('S01E02.fr.forced.srt' -> '01 - Titre.fr.forced.srt').
    """
    renames = []
    prefixe = video.stem.lower() + "."
    for f in naming.files_with_ext(video.parent, naming.SUBTITLE_EXTS):
        if not f.name.lower().startswith(prefixe):
            continue
        suite = f.name[len(video.stem):]
        suite = suite[:-len(f.suffix)] + f.suffix.lower()
        renames.append((f, f.with_name(new_stem + suite)))
    return renames


def plan_season(folder, season, threshold):
    """Prevoit les renommages d'un dossier. Retourne (planned, tally).

    `planned` = [(source, destination), ...], videos et sous-titres melanges.
    Deux fichiers qui visent le meme nom (deux versions du meme episode, par
    exemple) sont signales ici : au moment d'ecrire, le second echouerait sans
    explication.
    """
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    if not by_num:
        print("  aucune donnee d'episode TMDB pour cette saison")
        return [], Tally()

    width = max(2, len(str(max(by_num))))   # meme nb de digits pour toute la saison
    files = naming.files_with_ext(folder, naming.VIDEO_EXTS)
    if not files:
        print("  aucun fichier video")
        return [], Tally()

    planned, claimed, tally = [], {}, Tally(total=len(files))
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

        # Les sous-titres suivent meme quand la video, elle, est deja bien nommee.
        subs = [(src, cible) for src, cible in sidecar_renames(f, stem) if cible != src]
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
        renamed = apply_renames(planned)
        videos = {src for src, _ in planned if src.suffix.lower() in naming.VIDEO_EXTS}
        tally.named += len(videos & renamed)
        tally.subtitles = len(renamed - videos)   # ce qui a vraiment bouge
    return tally


# ----------------------------------------------------------------------------
# Programme principal
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Renomme les episodes d'une serie au format '{numero} - {nom}.ext' (donnees TMDB).")
    ap.add_argument("--dir", required=True,
                    help="Racine de la serie (dossiers 'Saison N') OU un dossier de saison")
    ap.add_argument("--tmdb-id", help="Identifiant TMDB de la serie "
                    "(par defaut : recherche sur le nom du dossier)")
    ap.add_argument("--language", default="fr-FR", help="Langue TMDB (defaut : fr-FR)")
    ap.add_argument("--no-cache", action="store_true",
                    help="Ignore le cache des reponses TMDB et le rafraichit")
    ap.add_argument("--apply", action="store_true", help="Renomme reellement (defaut : simulation)")
    ap.add_argument("--match-threshold", type=float, default=0.55,
                    help="Score minimal pour une association par titre (0-1)")
    args = ap.parse_args()

    cli.setup_console()
    cli.check_dir(args.dir)
    tmdb = Tmdb(cli.resolve_tmdb_key(TMDB_KEY), args.language, user_agent="rename_ep/1.0",
                cache=cache.Cache(read=not args.no_cache))

    mode = cli.mode_label(args, "rien ne sera renomme ; ajoute --apply")
    print(f"=== {mode} ===   source : TMDB {args.language}")
    args.tmdb_id = lookup.resolve_show_id(tmdb, args.dir, args.tmdb_id)
    if args.tmdb_id is None:
        sys.exit("Serie non identifiee : relance avec --tmdb-id.")
    print()

    seasons = naming.find_seasons(args.dir)
    bilan = Tally()
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
        num = 1 if num is None else num      # 0 = les speciaux, a ne pas confondre
        print(f"--- {Path(args.dir).name}  (TMDB saison {num}) ---")
        try:
            data = tmdb.season(args.tmdb_id, num)
        except TmdbError as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        bilan += rename_season(Path(args.dir), data, args)

    sous_titres = (f", {bilan.subtitles} sous-titre(s) "
                   + ("renomme(s)" if args.apply else "a renommer")) if bilan.subtitles else ""
    print(f"TOTAL : {bilan.named}/{bilan.total} fichier(s) au bon nom{sous_titres}.")


if __name__ == "__main__":
    try:
        main()
    except TmdbAuthError as e:
        sys.exit(f"TMDB : {e}")
