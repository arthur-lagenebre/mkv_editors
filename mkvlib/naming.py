"""Lecture des noms de fichiers et de dossiers : saisons, numeros d'episode,
titre/annee d'un film, et mise en forme des noms ecrits sur le disque.

Tout est pur (aucun acces reseau, aucun sous-processus) : c'est la partie du
depot la plus facile a casser silencieusement, et donc celle qui est testee.
"""

import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

# --------------------------------------------------------------------------
# Saisons
# --------------------------------------------------------------------------
# "Saison 1", "Season 02", "S1", "Saison_3"...
SEASON_RE = re.compile(r"^(?:saison|season|s)[\s_]*0*(\d+)$", re.IGNORECASE)

# Les episodes speciaux sont la saison 0 chez TMDB, mais le dossier porte
# rarement ce nom : "Specials" est ce que produisent la plupart des outils.
SPECIALS_RE = re.compile(r"^(?:specials?|hors[\s_-]?series?)$", re.IGNORECASE)


def season_number(name):
    """Numero de saison porte par un nom de dossier, ou None."""
    stem = Path(name).stem
    m = SEASON_RE.match(stem)
    if m:
        return int(m.group(1))
    return 0 if SPECIALS_RE.match(stem) else None


def find_seasons(root):
    """[(dossier_saison, numero), ...] pour chaque sous-dossier 'Saison N'.

    Liste vide si `root` ne contient aucun sous-dossier de saison : l'appelant en
    deduit que --dir pointe directement sur une saison unique.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    pairs = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        num = season_number(sub.name)
        if num is not None:
            pairs.append((sub, num))
    return sorted(pairs, key=lambda x: x[1])


# --------------------------------------------------------------------------
# Numero d'episode
# --------------------------------------------------------------------------
P_SE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")          # S01E05, S1E5
P_X = re.compile(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{2,3})(?!\d)")    # 1x05 (lookarounds : evite 1920x1080)
P_EP = re.compile(r"[Ee]p(?:isode)?[\s._-]*(\d{1,3})")            # Episode 5, Ep05
P_LEAD = re.compile(r"^\s*(\d{1,2})[\s._\-]")                     # 01 - Titre, 1. Titre, 05_Titre


def detect_episode_number(filename):
    """Numero d'episode lu dans le nom de fichier, ou None."""
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
    """Repli si le nom ne contient pas de numero : matche sur le titre TMDB.

    Retourne (episode, score). Le titre est aussi compare tronque a son premier
    ':' ("Warhammer 40,000: And They Shall..." -> "Warhammer 40,000"), forme sous
    laquelle il apparait souvent seule dans les noms de fichiers.
    """
    stem = Path(filename).stem.lower()
    best, best_score = None, 0.0
    for ep in episodes:
        title = (ep.get("name") or "").lower()
        head = title.split(":")[0].strip()
        score = max(SequenceMatcher(None, stem, title).ratio(),
                    SequenceMatcher(None, stem, head).ratio())
        if head and head in stem:       # le debut du titre est present tel quel
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = ep, score
    return best, best_score


# --------------------------------------------------------------------------
# Titre et annee d'un film
# --------------------------------------------------------------------------
ORDER_RE = re.compile(r"^\s*(\d{1,3})\s*[-–—]\s+")   # "1 - ", "01 - " (tiret obligatoire)

# Tokens de "release" a retirer du nom avant la recherche.
QUALITY_RE = re.compile(
    r"\b(1080p|2160p|4k|720p|480p|x265|x264|h ?265|h ?264|hevc|avc|aac|ac3|eac3|dts|truehd|"
    r"web[- ]?dl|web[- ]?rip|blu[- ]?ray|bdrip|brrip|hdrip|dvdrip|remux|multi|truefrench|"
    r"vff|vfi|vfq|vf2|vf|vostfr|vo|hdr10?|10bit|8bit|dolby|atmos|imax|extended|remastered)\b",
    re.IGNORECASE)

BARE_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def max_plausible_year():
    """Borne haute d'une annee de sortie : les films annonces vont rarement au-dela."""
    return date.today().year + 2


def parse_title_year(name):
    """'Inception (2010)' -> ('Inception', '2010', None). Annee entre () prioritaire.

    Un prefixe d'ordre de saga '{n} - ' est detecte et retire ('1 - Iron Man' -> ordre 1).

    Une annee "nue" (sans parentheses) n'est retenue que si elle est plausible :
    sinon 'Blade Runner 2049' serait cherche comme 'Blade Runner' sorti en 2049.
    Et si le nettoyage ne laisse aucun titre, c'est que le titre EST le nombre
    ('2012') : on le rend tel quel plutot que de chercher une chaine vide.
    """
    order = None
    mo = ORDER_RE.match(name)
    if mo:                                 # prefixe d'ordre "{n} - " -> retire du titre
        order = int(mo.group(1))
        name = name[mo.end():]
    mb = re.search(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]", name)
    if mb:                                 # annee entre parentheses/crochets = la bonne
        year, s = mb.group(1), name[:mb.start()]
    else:
        limit = max_plausible_year()
        bare = [m for m in BARE_YEAR_RE.finditer(name) if int(m.group(0)) <= limit]
        if bare:                           # sinon, derniere annee "nue" plausible
            year, s = bare[-1].group(0), name[:bare[-1].start()]
        else:
            year, s = None, name
    s = re.sub(r"[._]", " ", s)
    s = re.sub(r"[\(\)\[\]{}]", " ", s)
    s = QUALITY_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return (s or year or ""), year, order


# --------------------------------------------------------------------------
# Ecriture de noms
# --------------------------------------------------------------------------
# Identifiant TMDB epingle dans un nom de dossier ou de fichier. Les deux formes
# repandues sont acceptees : "[tmdbid-27205]" (Jellyfin) et "{tmdb-27205}" (Kodi).
TMDB_ID_RE = re.compile(r"[\[{]\s*tmdb(?:id)?[-=\s]\s*(\d+)\s*[\]}]", re.IGNORECASE)


def extract_tmdb_id(name):
    """(id epingle, nom debarrasse du marqueur). (None, nom) s'il n'y en a pas.

    Ecrire l'identifiant dans le nom du dossier est le seul moyen de corriger
    DURABLEMENT une recherche qui se trompe : il vaut pour tous les passages
    suivants, sans avoir a relancer ce titre a part.
    """
    m = TMDB_ID_RE.search(name)
    if not m:
        return None, name
    reste = (name[:m.start()] + " " + name[m.end():])
    return m.group(1), re.sub(r"\s+", " ", reste).strip(" -")


def safe_name(s):
    """Nettoie un titre pour en faire un nom de fichier valide sous Windows."""
    s = re.sub(r'[<>:"/\\|?*]', "", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(". ")


FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def fr_date(iso):
    """'2024-12-10' -> '10 décembre 2024'."""
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {FR_MONTHS[int(m) - 1]} {y}"
    except (AttributeError, ValueError, IndexError):
        return iso or ""


# --------------------------------------------------------------------------
# Fichiers d'une saison
# --------------------------------------------------------------------------
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts",
              ".flv", ".webm", ".mpg", ".mpeg", ".mts", ".vob", ".ogm"}

SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}


def files_with_ext(folder, extensions):
    """Fichiers d'un dossier dont l'extension figure dans `extensions`, tries.

    Un dossier illisible rend une liste vide : l'existence de --dir est verifiee
    une fois pour toutes au demarrage, le reste n'a pas a s'en soucier.
    """
    try:
        return sorted(f for f in Path(folder).iterdir()
                      if f.is_file() and f.suffix.lower() in extensions)
    except OSError:
        return []


def match_episode(filename, episodes, threshold, by_num=None):
    """(episode|None, methode) pour un fichier : par numero, sinon par titre.

    Le numero lu dans le nom prime ; a defaut on compare le nom aux titres TMDB
    et on n'accepte qu'au-dela de `threshold`.
    """
    if by_num is None:
        by_num = {e.get("episode_number"): e for e in episodes}
    num = detect_episode_number(filename)
    if num is not None and num in by_num:
        return by_num[num], f"n.{num:02d} (depuis le nom)"
    ep, score = best_title_match(filename, episodes)
    return (ep if score >= threshold else None), f"titre (~{score:.0%})"


def owned_numbers(folder, episodes, threshold=0.55):
    """Numeros d'episode presents sur le disque, quel que soit le format video.

    Sert a distinguer, dans la fiche recap, ce qu'on possede de ce qui manque -
    sans rien lire dans les fichiers.
    """
    by_num = {e.get("episode_number"): e for e in episodes}
    owned = set()
    for f in files_with_ext(folder, VIDEO_EXTS):
        ep, _ = match_episode(f.name, episodes, threshold, by_num)
        if ep is not None:
            owned.add(ep.get("episode_number"))
    return owned
