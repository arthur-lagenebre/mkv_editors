"""Lecture des noms de fichiers et de dossiers : saisons, numeros d'episode,
titre/annee d'un film, et mise en forme des noms ecrits sur le disque.

Tout est pur (aucun acces reseau, aucun sous-processus) : c'est la partie du
depot la plus facile a casser silencieusement, et donc celle qui est testee.
"""

import re
from dataclasses import dataclass, field
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
# Films sur le disque
# --------------------------------------------------------------------------
# Un bonus se reconnait a son NOM, jamais a son poids : dans une vraie
# mediatheque, un film d'animation de 1 Go voisine avec un remux de 28 Go, et
# tout seuil relatif finit par jeter des films (c'est ce qui a fait disparaitre
# 'Catwoman' d'un dossier ou trainait 'Constantine' en remux).

# Dossiers qui n'ont jamais de film a etiqueter : bonus des editions, dossiers
# techniques des NAS. Descendre dedans reviendrait a etiqueter des featurettes.
SKIP_DIR_RE = re.compile(
    r"^(?:extras?|bonus|suppl[eé]ments?|featurettes?|behind[\s._-]*the[\s._-]*scenes|"
    r"making[\s._-]*of|trailers?|bandes?[\s._-]*annonces?|samples?|@eadir)$", re.IGNORECASE)

# Noms de bonus poses a cote du film, dans le meme dossier.
EXTRA_NAME_RE = re.compile(
    r"\b(?:bande[\s._-]*annonce|trailer|teaser|making[\s._-]*of|featurette|sample|"
    r"bonus|interview|sc[eè]nes?[\s._-]*coup[eé]es?|deleted[\s._-]*scenes)\b",
    re.IGNORECASE)

# Marqueur de part d'un film coupe en plusieurs fichiers : CD1, Disc 2, Partie 3.
PART_MARK_RE = re.compile(r"\b(?:cd|dvd|disc|disque|part|partie|pt)[\s._-]*\d{1,2}\b",
                          re.IGNORECASE)


@dataclass
class MovieFolder:
    """Un film sur le disque : ses fichiers video, et le nom a interpreter.

    `rawname` est le nom du DOSSIER quand celui-ci ne contient que ce film - c'est
    la que vit le titre dans "Inception (2010)/film.mkv" - et le nom du FICHIER
    partout ailleurs. Dans ce second cas, `contexts` liste les dossiers au-dessus
    du film, du plus proche au plus lointain ; la recherche TMDB ne se sert que
    du premier (voir lookup.MAX_CONTEXTS), les autres etant des dossiers de
    rangement de la mediatheque plutot que des sagas.
    """
    folder: Path
    files: list
    rawname: str
    contexts: list = field(default_factory=list)
    owns_folder: bool = False
    display: str = ""

    def __post_init__(self):
        self.display = self.display or self.rawname


def without_part_mark(stem):
    """'Heat CD2' -> 'Heat'. Chaine vide si le nom n'etait QUE ca ('CD1')."""
    return " ".join(PART_MARK_RE.sub(" ", stem).split()).strip(" -_.")


def strip_part_mark(stem):
    """Nom a chercher pour un fichier : sans son marqueur de part, s'il en reste."""
    return without_part_mark(stem) or stem


def part_key(path):
    """Cle de regroupement d'un fichier : son nom sans marqueur de part.

    'Heat CD1' et 'Heat CD2' la partagent - c'est un seul film en deux morceaux -
    et 'CD1'/'CD2' aussi, vide. '1 - Joker' et '2 - Folie a deux' ne la partagent
    pas : ce sont deux films, et un ordre de saga n'est pas un marqueur de part.
    """
    return without_part_mark(path.stem).lower()


def is_extra(path, size, reference):
    """Ce fichier est-il un bonus pose a cote du film ?

    Son nom doit le dire, et il ne doit pas etre le plus gros de son dossier :
    un titre a le droit de contenir 'Trailer' ou 'Bonus', et s'il n'y a pas de
    film a cote, c'est lui le film. Un .mkv qui ne dit rien reste un film : mal
    associe, il se signale ; ecarte, il disparait sans un mot.
    """
    return size < reference and bool(EXTRA_NAME_RE.search(path.stem))


def movie_groups(mkvs):
    """[[fichiers d'un film], ...] pour les .mkv d'UN dossier, bonus ecartes.

    Deux pieges opposes se referment ici. Etiqueter un CD2 comme un film a part :
    il n'a pas de titre, la recherche part a l'aveugle. Et etiqueter deux films
    d'une saga comme les deux parts d'un seul : ils recoivent alors les MEMES
    metadonnees, ce qui a longtemps ecrit 'Joker' sur 'Folie a deux'.
    """
    tailles = {}
    for f in mkvs:
        try:
            tailles[f] = f.stat().st_size
        except OSError:                       # fichier disparu ou partage coupe
            tailles[f] = 0
    if not tailles:
        return []
    reference = max(tailles.values())
    groupes = {}
    for f in sorted(tailles):
        if is_extra(f, tailles[f], reference):
            continue
        groupes.setdefault(part_key(f), []).append(f)
    return list(groupes.values())


def subdirs(folder):
    """Sous-dossiers a explorer, tries : ni bonus, ni dossiers caches."""
    try:
        subs = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in subs
            if not p.name.startswith(".") and not SKIP_DIR_RE.match(p.name)]


def _movie_entry(folder, root, files, owns):
    """Construit le MovieFolder d'un groupe de fichiers deja constitue."""
    rawname = folder.name if owns else strip_part_mark(files[0].stem)
    try:
        rel = folder.relative_to(root)
    except ValueError:                        # ne devrait pas arriver : par securite
        rel = Path(folder.name)
    # Les dossiers au-dessus du film, du plus proche au plus lointain. Celui qui
    # prete deja son nom au film n'apprendrait rien de plus, et --dir lui-meme
    # porte le nom de la mediatheque, pas d'une saga : ni l'un ni l'autre n'y est.
    parents = list(rel.parts)[:-1] if owns else list(rel.parts)
    return MovieFolder(folder=folder, files=files, rawname=rawname,
                       contexts=list(reversed(parents)),
                       owns_folder=owns,
                       display=str(rel if owns else rel / rawname))


def _scan(folder, root):
    """Films de `folder` puis de tout ce qu'il contient, en profondeur."""
    nested = []
    for sub in subdirs(folder):
        nested += _scan(sub, root)
    groupes = movie_groups(files_with_ext(folder, {".mkv"}))
    # Un dossier ne parle pour un film que s'il n'en contient qu'un ET ne cache
    # rien en dessous : sinon c'est un dossier de saga ou de rangement, et chaque
    # fichier repond de lui-meme. --dir lui-meme ne parle jamais : il porte le nom
    # de la mediatheque ("_DC"), pas celui d'un film.
    owns = len(groupes) == 1 and not nested and folder != root
    return [_movie_entry(folder, root, files, owns) for files in groupes] + nested


def find_movies(root):
    """[MovieFolder, ...] : tous les films sous `root`, sous-dossiers compris.

    La descente est recursive et sans limite de profondeur : une mediatheque se
    range par saga ("_DC/DCEU/01 - Man of Steel.mkv"), parfois sur deux etages
    ("_DC/Batman/Nolan Trilogy/..."), et les films a plat cotoient les dossiers.

    Un DOSSIER n'est jamais un film : il ne compte pas et ne se traite pas. Il
    prete seulement son nom au film qu'il contient, quand il n'en contient qu'un.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    return _scan(root, root)


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
# "1 - ", "01 - ", le demi-numero des films intercalaires ("1.5 - Dark Fury"), et
# l'annee quand c'est elle qui ordonne la saga ("1990 - Les Tortues Ninja").
ORDER_RE = re.compile(r"^\s*(\d{1,4}(?:\.\d{1,2})?)\s*[-–—]\s+")   # tiret obligatoire

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

    Un prefixe d'ordre de saga '{n} - ' est detecte et retire ('1 - Iron Man' -> ordre 1),
    demi-numeros compris : '1.5 - Dark Fury' rend l'ordre '1.5', un film intercalaire.

    Une annee "nue" (sans parentheses) n'est retenue que si elle est plausible ET
    suivie de quelque chose. Un nombre qui TERMINE le nom appartient au titre :
    "Wonder Woman 1984", "New York 1997", "Blade Runner 2049". Une annee de
    release, elle, est suivie des tokens de qualite ("dune.2021.1080p.WEB-DL"),
    et une annee voulue se met entre parentheses.
    Et si le nettoyage ne laisse aucun titre, c'est que le titre EST le nombre
    ('2012') : on le rend tel quel plutot que de chercher une chaine vide.
    """
    order = None
    mo = ORDER_RE.match(name)
    if mo:                                 # prefixe d'ordre "{n} - " -> retire du titre
        brut = mo.group(1)                 # "1.5" reste tel quel : ce n'est pas un entier
        order = int(brut) if brut.isdigit() else brut
        name = name[mo.end():]
    mb = re.search(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]", name)
    if mb:                                 # annee entre parentheses/crochets = la bonne
        year, s = mb.group(1), name[:mb.start()]
    else:
        limit = max_plausible_year()
        bare = [m for m in BARE_YEAR_RE.finditer(name) if int(m.group(0)) <= limit]
        # Un nombre qui TERMINE le nom appartient au titre : "Wonder Woman 1984",
        # "New York 1997", "Death Race 2000". Une annee, elle, est suivie de
        # quelque chose - les tokens de release - ou mise entre parentheses.
        if bare and name[bare[-1].end():].strip():
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
