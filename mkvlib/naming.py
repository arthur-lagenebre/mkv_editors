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


def season_number(name):
    """Numero de saison porte par un nom de dossier, ou None."""
    m = SEASON_RE.match(Path(name).stem)
    return int(m.group(1)) if m else None


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
