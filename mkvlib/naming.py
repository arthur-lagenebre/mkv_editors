"""Lecture des noms de fichiers et de dossiers : saisons, numéros d'épisode, titre/année d'un film, et mise en forme des noms écrits sur le disque.

Tout est pur (aucun accès réseau, aucun sous-processus) : c'est la partie du dépôt la plus facile à casser silencieusement, et donc celle qui est testée.
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
# Les épisodes spéciaux sont la saison 0 chez TMDB, mais le dossier porte rarement ce nom : "Specials" est ce que produisent la plupart des outils.
SPECIALS_RE = re.compile(r"^(?:specials?|hors[\s_-]?series?)$", re.IGNORECASE)

def season_number(name):
    """Numéro de saison porte par un nom de dossier, ou None."""
    stem = Path(name).stem
    m = SEASON_RE.match(stem)
    if m:
        return int(m.group(1))
    return 0 if SPECIALS_RE.match(stem) else None

def find_seasons(root):
    """[(dossier_saison, numéro), ...] pour chaque sous-dossier 'Saison N'.

    Liste vide si `root` ne contient aucun sous-dossier de saison : l'appelant en deduit que --dir pointe directement sur une saison unique.
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
# Un bonus se reconnaît à son NOM, jamais à son poids : dans une vraie médiathèque, un film d'animation de 1 Go voisine avec un remux de 28 Go, et tout seuil relatif finit par jeter des films (c'est ce qui a fait disparaître 'Catwoman' d'un dossier ou traînait 'Constantine' en remux).

# Dossiers qui n'ont jamais de film à étiqueter : bonus des éditions, dossiers techniques des NAS. Descendre dedans reviendrait à étiqueter des featurettes.
SKIP_DIR_RE = re.compile(r"^(?:extras?|bonus|suppl[eé]ments?|featurettes?|behind[\s._-]*the[\s._-]*scenes|making[\s._-]*of|trailers?|bandes?[\s._-]*annonces?|samples?|@eadir)$", re.IGNORECASE)
# Noms de bonus posés à côté du film, dans le même dossier.
EXTRA_NAME_RE = re.compile(r"\b(?:bande[\s._-]*annonce|trailer|teaser|making[\s._-]*of|featurette|sample|bonus|interview|sc[eè]nes?[\s._-]*coup[eé]es?|deleted[\s._-]*scenes)\b", re.IGNORECASE)
# Marqueur de part d'un film coupe en plusieurs fichiers : CD1, Disc 2, Partie 3.
PART_MARK_RE = re.compile(r"\b(?:cd|dvd|disc|disque|part|partie|pt)[\s._-]*\d{1,2}\b", re.IGNORECASE)

@dataclass
class MovieFolder:
    """Un film sur le disque : ses fichiers vidéo, et le nom à interpréter.

    `rawname` est le nom du DOSSIER quand celui-ci ne contient que ce film - c'est la que vit le titre dans "Inception (2010)/film.mkv" - et le nom du FICHIER partout ailleurs. Dans ce second cas, `contexts` liste les dossiers au-dessus du film, du plus proche au plus lointain ; la recherche TMDB ne se sert que du premier (voir lookup.MAX_CONTEXTS), les autres étant des dossiers de rangement de la médiathèque plutôt que des sagas.
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
    """'Heat CD2' -> 'Heat'. Chaîne vide si le nom n'était QUE ça ('CD1')."""
    return " ".join(PART_MARK_RE.sub(" ", stem).split()).strip(" -_.")

def strip_part_mark(stem):
    """Nom à chercher pour un fichier : sans son marqueur de part, s'il en reste."""
    return without_part_mark(stem) or stem

def part_key(path):
    """Clé de regroupement d'un fichier : son nom sans marqueur de part.

    'Heat CD1' et 'Heat CD2' la partagent - c'est un seul film en deux morceaux - et 'CD1'/'CD2' aussi, vide. '1 - Joker' et '2 - Folie à deux' ne la partagent pas : ce sont deux films, et un ordre de saga n'est pas un marqueur de part.
    """
    return without_part_mark(path.stem).lower()

def is_extra(path, size, reference):
    """Ce fichier est-il un bonus posé à côté du film ?

    Son nom doit le dire, et il ne doit pas être le plus gros de son dossier : un titre à le droit de contenir 'Trailer' ou 'Bonus', et s'il n'y a pas de film à côté, c'est lui le film. Un .mkv qui ne dit rien reste un film : mal associe, il se signale ; écarte, il disparaît sans un mot.
    """
    return size < reference and bool(EXTRA_NAME_RE.search(path.stem))


def movie_groups(mkvs):
    """[[fichiers d'un film], ...] pour les .mkv d'UN dossier, bonus écartés.

    Deux pièges opposés se referment ici. Étiqueter un CD2 comme un film à part : il n'a pas de titre, la recherche part à l'aveugle. Et étiqueter deux films d'une saga comme les deux parts d'un seul : ils reçoivent alors les MÊMES métadonnées, ce qui a longtemps écrit 'Joker' sur 'Folie à deux'.
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
    """Sous-dossiers à explorer, triés : ni bonus, ni dossiers caches."""
    try:
        subs = sorted(p for p in folder.iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in subs if not p.name.startswith(".") and not SKIP_DIR_RE.match(p.name)]

def _movie_entry(folder, root, files, owns):
    """Construit le MovieFolder d'un groupe de fichiers déjà constitué."""
    rawname = folder.name if owns else strip_part_mark(files[0].stem)
    try:
        rel = folder.relative_to(root)
    except ValueError:                        # ne devrait pas arriver : par sécurité
        rel = Path(folder.name)
    # Les dossiers au-dessus du film, du plus proche au plus lointain. Celui qui prête déjà son nom au film n'apprendrait rien de plus, et --dir lui-même porte le nom de la médiathèque, pas d'une saga : ni l'un ni l'autre n'y est.
    parents = list(rel.parts)[:-1] if owns else list(rel.parts)
    return MovieFolder(folder=folder, files=files, rawname=rawname, contexts=list(reversed(parents)), owns_folder=owns, display=str(rel if owns else rel / rawname))

def _scan(folder, root):
    """Films de `folder` puis de tout ce qu'il contient, en profondeur."""
    nested = []
    for sub in subdirs(folder):
        nested += _scan(sub, root)
    groupes = movie_groups(files_with_ext(folder, {".mkv"}))
    # Un dossier ne parle pour un film que s'il n'en contient qu'un ET ne cache rien en dessous : sinon c'est un dossier de saga ou de rangement, et chaque fichier répond de lui-même. --dir lui-même ne parle jamais : il porte le nom de la médiathèque ("_DC"), pas celui d'un film.
    owns = len(groupes) == 1 and not nested and folder != root
    return [_movie_entry(folder, root, files, owns) for files in groupes] + nested

def find_movies(root):
    """[MovieFolder, ...] : tous les films sous `root`, sous-dossiers compris.

    La descente est récursive et sans limite de profondeur : une médiathèque se range par saga ("_DC/DCEU/01 - Man of Steel.mkv"), parfois sur deux étages ("_DC/Batman/Nolan Trilogy/..."), et les films à plat côtoient les dossiers.

    Un DOSSIER n'est jamais un film : il ne compte pas et ne se traite pas. Il prête seulement son nom au film qu'il contient, quand il n'en contient qu'un.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    return _scan(root, root)

# --------------------------------------------------------------------------
# Numéro d'épisode
# --------------------------------------------------------------------------
P_SE = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")          # S01E05, S1E5
P_X = re.compile(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{2,3})(?!\d)")    # 1x05 (lookarounds : évite 1920x1080)
P_EP = re.compile(r"[Ee]p(?:isode)?[\s._-]*(\d{1,3})")            # Épisode 5, Ep05
P_LEAD = re.compile(r"^\s*(\d{1,2})[\s._\-]")                     # 01 - Titre, 1. Titre, 05_Titre

def detect_episode_number(filename):
    """Numéro d'épisode lu dans le nom de fichier, ou None."""
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
    """Repli si le nom ne contient pas de numéro : matche sur le titre TMDB.

    Retourne (épisode, score). Le titre est aussi compare tronque à son premier ':' ("Warhammer 40,000: And They Shall..." -> "Warhammer 40,000"), forme sous laquelle il apparait souvent seule dans les noms de fichiers.
    """
    stem = Path(filename).stem.lower()
    best, best_score = None, 0.0
    for ep in episodes:
        title = (ep.get("name") or "").lower()
        head = title.split(":")[0].strip()
        score = max(SequenceMatcher(None, stem, title).ratio(), SequenceMatcher(None, stem, head).ratio())
        if head and head in stem:       # le début du titre est présent tel quel
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = ep, score
    return best, best_score


# --------------------------------------------------------------------------
# Titre et année d'un film
# --------------------------------------------------------------------------
# "1 - ", "01 - ", le demi-numéro des films intercalaires ("1.5 - Dark Fury"), et l'année quand c'est elle qui ordonne la saga ("1990 - Les Tortues Ninja").
ORDER_RE = re.compile(r"^\s*(\d{1,4}(?:\.\d{1,2})?)\s*[-–—]\s+")   # tiret obligatoire
# Tokens de "release" à retirer du nom avant la recherche.
QUALITY_RE = re.compile(r"\b(1080p|2160p|4k|720p|480p|x265|x264|h ?265|h ?264|hevc|avc|aac|ac3|eac3|dts|truehd|web[- ]?dl|web[- ]?rip|blu[- ]?ray|bdrip|brrip|hdrip|dvdrip|remux|multi|truefrench|vff|vfi|vfq|vf2|vf|vostfr|vo|hdr10?|10bit|8bit|dolby|atmos|imax|extended|remastered)\b", re.IGNORECASE)
BARE_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

def max_plausible_year():
    """Borne haute d'une année de sortie : les films annoncés vont rarement au-delà."""
    return date.today().year + 2

def parse_title_year(name):
    """'Inception (2010)' -> ('Inception', '2010', None). Année entre () prioritaire.

    Un préfixe d'ordre de saga '{n} - ' est détecté et retire ('1 - Iron Man' -> ordre 1), demi-numéros compris : '1.5 - Dark Fury' rend l'ordre '1.5', un film intercalaire.

    Une année "nue" (sans parenthèses) n'est retenue que si elle est plausible ET suivie de quelque chose. Un nombre qui TERMINE le nom appartient au titre : "Wonder Woman 1984", "New York 1997", "Blade Runner 2049". Une année de release, elle, est suivie des tokens de qualité ("dune.2021.1080p.WEB-DL"), et une année voulue se met entre parenthèses. Et si le nettoyage ne laisse aucun titre, c'est que le titre EST le nombre ('2012') : on le rend tel quel plutôt que de chercher une chaîne vide.
    """
    order = None
    mo = ORDER_RE.match(name)
    if mo:                                 # préfixe d'ordre "{n} - " -> retire du titre
        brut = mo.group(1)                 # "1.5" reste tel quel : ce n'est pas un entier
        order = int(brut) if brut.isdigit() else brut
        name = name[mo.end():]
    mb = re.search(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]", name)
    if mb:                                 # année entre parenthèses/crochets = la bonne
        year, s = mb.group(1), name[:mb.start()]
    else:
        limit = max_plausible_year()
        bare = [m for m in BARE_YEAR_RE.finditer(name) if int(m.group(0)) <= limit]
        # Un nombre qui TERMINE le nom appartient au titre : "Wonder Woman 1984", "New York 1997", "Death Race 2000". Une année, elle, est suivie de quelque chose - les tokens de release - ou mise entre parenthèses.
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
# Écriture de noms
# --------------------------------------------------------------------------
# Identifiant TMDB épinglé dans un nom de dossier ou de fichier. Les deux formes répandues sont acceptées : "[tmdbid-27205]" (Jellyfin) et "{tmdb-27205}" (Kodi).
TMDB_ID_RE = re.compile(r"[\[{]\s*tmdb(?:id)?[-=\s]\s*(\d+)\s*[\]}]", re.IGNORECASE)

def extract_tmdb_id(name):
    """(id épinglé, nom débarrasse du marqueur). (None, nom) s'il n'y en a pas.

    Écrire l'identifiant dans le nom du dossier est le seul moyen de corriger DURABLEMENT une recherche qui se trompe : il vaut pour tous les passages suivants, sans avoir à relancer ce titre à part.
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

FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]

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
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".flv", ".webm", ".mpg", ".mpeg", ".mts", ".vob", ".ogm"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}

def files_with_ext(folder, extensions):
    """Fichiers d'un dossier dont l'extension figure dans `extensions`, triés.

    Un dossier illisible rend une liste vide : l'existence de --dir est vérifiée une fois pour toutes au démarrage, le reste n'a pas à s'en soucier.
    """
    try:
        return sorted(f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() in extensions)
    except OSError:
        return []

def relative_name(path, root):
    """Chemin affichable : ce qui distingue le fichier, sans le préfixe commun."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:                               # hors de la racine (lien, montage)
        return str(path)

def match_episode(filename, episodes, threshold, by_num=None):
    """(épisode|None, méthode) pour un fichier : par numéro, sinon par titre.

    Le numéro lu dans le nom prime ; à défaut on compare le nom aux titres TMDB et on n'accepte qu'au-delà de `threshold`.
    """
    if by_num is None:
        by_num = {e.get("episode_number"): e for e in episodes}
    num = detect_episode_number(filename)
    if num is not None and num in by_num:
        return by_num[num], f"n.{num:02d} (depuis le nom)"
    ep, score = best_title_match(filename, episodes)
    return (ep if score >= threshold else None), f"titre (~{score:.0%})"


def owned_numbers(folder, episodes, threshold=0.55):
    """Numéros d'épisode présents sur le disque, quel que soit le format vidéo.

    Sert à distinguer, dans la fiche récap, ce qu'on possède de ce qui manque - sans rien lire dans les fichiers.
    """
    by_num = {e.get("episode_number"): e for e in episodes}
    owned = set()
    for f in files_with_ext(folder, VIDEO_EXTS):
        ep, _ = match_episode(f.name, episodes, threshold, by_num)
        if ep is not None:
            owned.add(ep.get("episode_number"))
    return owned
