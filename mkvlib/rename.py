"""Renommer des fichiers et des dossiers sans rien perdre en route.

Trois pieges que le renommage naif ignore : sous Windows, changer seulement la
casse d'un nom est refuse ; un fichier peut viser le nom qu'un autre porte
encore (numerotation decalee d'un cran) ; et les sous-titres poses a cote d'une
video doivent la suivre, sinon le lecteur ne les associe plus.
"""

import os
from dataclasses import dataclass

from . import naming


def free_name(path):
    """Nom temporaire libre a cote de `path`, pour un renommage en deux temps."""
    for i in range(1, 1000):
        candidate = path.with_name(f"{path.stem}.tmp{i}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("aucun nom temporaire libre")


def same_file(a, b):
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
    if same_file(src, dst):
        tmp = free_name(src)
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
            if dst.exists() and not same_file(src, dst):
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
            tmp = free_name(src)
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
    """Compte-rendu d'un lot de renommages. Additionnable pour totaliser."""
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


