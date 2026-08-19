#!/usr/bin/env python3
"""
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
  python Rename_Episodes.py --dir "D:\\Series\\Ma Serie" --tmdb-id 1234           # simulation
  python Rename_Episodes.py --dir "D:\\Series\\Ma Serie" --tmdb-id 1234 --apply   # renomme
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from difflib import SequenceMatcher
from urllib.request import urlopen, Request
from urllib.error import URLError

# ============================================================================
# Cle API TMDB (par priorite : .env > env TMDB_API_KEY > ceci).
TMDB_KEY = ""
# ============================================================================

def load_dotenv(filename=".env"):
    """Charge un fichier .env (lignes CLE=valeur) dans les variables d'environnement.

    Le fichier est cherche en remontant depuis le dossier du script, puis depuis le
    dossier courant ; on s'arrete au premier trouve. Les variables deja definies
    dans l'environnement ne sont jamais ecrasees. Aucune dependance pip.
    """
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        for folder in (start, *start.parents):
            path = folder / filename
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name, value = name.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if name:
                    os.environ.setdefault(name, value)
            return path
    return None


TMDB_API = "https://api.themoviedb.org/3"

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts",
              ".flv", ".webm", ".mpg", ".mpeg", ".mts", ".vob", ".ogm"}

# Detection du numero d'episode dans un nom de fichier.
P_SE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")          # S01E05, S1E5
P_X = re.compile(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{2,3})(?!\d)")     # 1x05  (evite 1920x1080)
P_EP = re.compile(r"[Ee]p(?:isode)?[\s._-]*(\d{1,3})")            # Episode 5, Ep05
P_LEAD = re.compile(r"^\s*(\d{1,2})[\s._\-]")                     # 01 - Titre, 1. Titre
SEASON_RE = re.compile(r"^(?:saison|season|s)[\s_]*0*(\d+)$", re.IGNORECASE)


# ----------------------------------------------------------------------------
# Detection des saisons
# ----------------------------------------------------------------------------
def _season_number(name):
    m = SEASON_RE.match(Path(name).stem)
    return int(m.group(1)) if m else None


def find_seasons(root):
    """[(dossier_saison, numero), ...] pour chaque sous-dossier 'Saison N' ; [] sinon."""
    root = Path(root)
    if not root.is_dir():
        return []
    pairs = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        num = _season_number(sub.name)
        if num is not None:
            pairs.append((sub, num))
    return sorted(pairs, key=lambda x: x[1])


# ----------------------------------------------------------------------------
# Source TMDB
# ----------------------------------------------------------------------------
def _tmdb_get(endpoint, key, language):
    url = f"{TMDB_API}/{endpoint}"
    sep = "&" if "?" in url else "?"
    headers = {"User-Agent": "rename_ep/1.0", "Accept": "application/json"}
    if key.startswith("ey") and key.count(".") == 2:          # token v4
        headers["Authorization"] = f"Bearer {key}"
        url += f"{sep}language={language}"
    else:                                                     # cle v3
        url += f"{sep}api_key={key}&language={language}"
    with urlopen(Request(url, headers=headers), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def tmdb_season(show_id, season_number, key, language):
    return _tmdb_get(f"tv/{show_id}/season/{season_number}", key, language)


# ----------------------------------------------------------------------------
# Association fichier <-> episode
# ----------------------------------------------------------------------------
def detect_episode_number(filename):
    for pat in (P_SE, P_X):
        m = pat.search(filename)
        if m:
            return int(m.group(2))
    for pat in (P_EP, P_LEAD):
        m = pat.search(filename)
        if m:
            return int(m.group(1))
    return None


def best_title_match(filename, episodes):
    stem = Path(filename).stem.lower()
    best, best_score = None, 0.0
    for ep in episodes:
        title = ep.get("name", "").lower()
        head = title.split(":")[0].strip()
        score = max(SequenceMatcher(None, stem, title).ratio(),
                    SequenceMatcher(None, stem, head).ratio())
        if head and head in stem:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = ep, score
    return best, best_score


# ----------------------------------------------------------------------------
# Renommage
# ----------------------------------------------------------------------------
def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*]', "", s or "")   # caracteres interdits sous Windows
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(". ")


def rename_season(folder, season, args):
    """Renomme les videos d'un dossier au format '{NN} - {nom}.ext'. Retourne (ok, total)."""
    episodes = season.get("episodes", [])
    by_num = {e.get("episode_number"): e for e in episodes}
    if not by_num:
        print("  aucune donnee d'episode TMDB pour cette saison")
        return 0, 0
    width = max(2, len(str(max(by_num))))   # meme nb de digits pour toute la saison

    files = sorted(f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_EXTS)
    if not files:
        print("  aucun fichier video")
        return 0, 0

    planned, done = [], 0
    for f in files:
        num = detect_episode_number(f.name)
        ep = by_num.get(num)
        if ep is None:
            ep, score = best_title_match(f.name, episodes)
            if score < args.match_threshold:
                ep = None
        if ep is None:
            print(f"  [NON ASSOCIE] {f.name}")
            continue
        n = ep.get("episode_number", 0)
        newname = f"{n:0{width}d} - {safe_name(ep.get('name', ''))}{f.suffix.lower()}"
        dst = f.with_name(newname)
        if dst.name == f.name:
            done += 1                        # deja au bon nom
            continue
        planned.append((f, dst))
        print(f"  E{n:0{width}d} : {f.name}")
        print(f"          -> {newname}")

    if args.apply:
        for src, dst in planned:
            if dst.exists():
                print(f"  [IGNORE] existe deja : {dst.name}")
                continue
            src.rename(dst)
            done += 1

    return done, len(files)


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

    load_dotenv()  # rend disponibles les cles du fichier .env (non committe)
    args.tmdb_key = (os.environ.get("TMDB_API_KEY")
                     or os.environ.get("TMDB_KEY") or TMDB_KEY or None)
    if not args.tmdb_key:
        sys.exit("Aucune cle TMDB. Renseigne la ligne TMDB_KEY=... du fichier .env "
                 "(voir .env.example), la variable d'environnement TMDB_API_KEY, "
                 "ou la constante TMDB_KEY en haut du fichier.")

    mode = "APPLICATION" if args.apply else "SIMULATION (rien ne sera renomme ; ajoute --apply)"
    print(f"=== {mode} ===   source : TMDB {args.language}\n")

    seasons = find_seasons(args.dir)
    total_done = total = 0
    if seasons:
        for sub, num in seasons:
            print(f"--- {sub.name}  (TMDB saison {num}) ---")
            try:
                data = tmdb_season(args.tmdb_id, num, args.tmdb_key, args.language)
            except (URLError, OSError) as e:
                print(f"  echec TMDB saison {num} : {e} -> saison ignoree\n")
                continue
            d, t = rename_season(sub, data, args)
            total_done += d
            total += t
            print()
    else:
        num = _season_number(Path(args.dir).name) or 1
        print(f"--- {Path(args.dir).name}  (TMDB saison {num}) ---")
        try:
            data = tmdb_season(args.tmdb_id, num, args.tmdb_key, args.language)
        except (URLError, OSError) as e:
            sys.exit(f"Echec de l'appel TMDB (saison {num}) : {e}")
        d, t = rename_season(Path(args.dir), data, args)
        total_done += d
        total += t

    print(f"TOTAL : {total_done}/{total} fichier(s) au bon nom.")


if __name__ == "__main__":
    main()
