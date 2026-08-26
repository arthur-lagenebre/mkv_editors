"""Lecture de la STRUCTURE d'un .mkv, sans passer par MKVToolNix.

Aucun outil de MKVToolNix ne sait dire EN LECTURE SEULE qu'un conteneur est abîmé. Mesure faite sur des fichiers volontairement cassés : mkvmerge (même en démultiplexant tout vers NUL) et mkvinfo se resynchronisent en silence et rendent 0 ; seul "mkvpropedit --add-track-statistics-tags" proteste - mais il ÉCRIT dans le fichier, et il laisse passer une troncature. D'où ce module : il suit la chaîne des éléments et dit où elle se rompt.

Un fichier Matroska est une suite d'éléments "identifiant + taille + contenu", emboîtés. Les enfants du Segment se suivent bout à bout : chacun annonce sa taille, donc la position du suivant. Une chaîne saine part du premier élément et tombe sur la fin EXACTE du fichier. Si elle tombe ailleurs, ou sur un identifiant qui n'existe pas à ce niveau, le fichier est abîmé. Tout le contrôle tient dans cette phrase.

Deux profondeurs, parce que la lecture au hasard coûte cher sur un partage réseau (~70 ms par saut sur le NAS mesuré, soit plus de huit minutes pour parcourir les 2565 clusters d'un film de 9 Go) : - rapide  : une dizaine de lectures aux points de repère - l'index SeekHead, la table Cues, la queue du fichier. Le prix ne dépend pas de la taille. - complet : toute la chaîne, contenu des clusters compris, en avançant par lectures séquentielles plutôt que par sauts. Voit tout ce que voit mkvpropedit, plus la troncature, et n'écrit rien.
"""

import os
from dataclasses import dataclass

# Enfants directs du Segment. Un autre identifiant à ce niveau signe un fichier abîmé : la chaîne est retombée au milieu de données.
NIVEAU1 = {
    0x114D9B74: "SeekHead",
    0x1549A966: "Info",
    0x1654AE6B: "Tracks",
    0x1F43B675: "Cluster",
    0x1C53BB6B: "Cues",
    0x1941A469: "Attachments",
    0x1043A770: "Chapters",
    0x1254C367: "Tags",
    0xEC: "Void",
    0xBF: "CRC-32",
}

# Enfants d'un cluster, examinés seulement en contrôle complet : ils ne sont lisibles qu'une fois le contenu du cluster en main.
ENFANTS_CLUSTER = {0xE7: "Timestamp", 0xA3: "SimpleBlock", 0xA0: "BlockGroup", 0xAB: "PrevSize", 0xA7: "Position", 0x58D7: "SilentTracks", 0xEC: "Void", 0xBF: "CRC-32",}

ENTETE_EBML = 0x1A45DFA3
SEGMENT = 0x18538067
SEEK_HEAD = 0x114D9B74
CUES = 0x1C53BB6B
CLUSTER = 0x1F43B675

# Éléments internes de l'index et de la table Cues, seuls à être descendus.
SEEK = 0x4DBB
SEEK_ID = 0x53AB
SEEK_POSITION = 0x53AC
CUE_POINT = 0xBB
CUE_TRACK_POSITIONS = 0xB7
CUE_CLUSTER_POSITION = 0xF1

TAILLE_INCONNUE = -1
MAX_ENTETE = 12          # 4 octets d'identifiant + 8 de taille, au maximum
MAX_INDEX = 1 << 22      # au-delà, la taille annoncée pour un index est aberrante
MORCEAU = 1 << 22        # 4 Mo : de quoi lire vite sans charger un cluster entier


@dataclass(frozen=True)
class Defaut:
    """Un point de rupture dans la structure, et l'octet où il se trouve."""
    message: str
    position: int = -1

    def __str__(self):
        if self.position < 0:
            return self.message
        return f"{self.message} (octet {self.position})"


def lire_vint(bloc, j, garder_marqueur):
    """(valeur, longueur) de l'entier à taille variable posé à l'octet j.

    Le premier octet porte sa propre longueur : le rang de son bit le plus haut donne le nombre d'octets à lire. Un premier octet nul ne code rien de licite. L'identifiant garde ses bits de longueur - c'est ainsi qu'il est écrit dans les spécifications - la taille les perd. Une taille dont tous les bits restants valent 1 se dit inconnue : légitime pour un flux en direct.
    """
    if j < 0 or j >= len(bloc) or bloc[j] == 0:
        return None, 0
    longueur = 9 - bloc[j].bit_length()
    if j + longueur > len(bloc):
        return None, 0
    valeur = int.from_bytes(bloc[j:j + longueur], "big")
    if not garder_marqueur:
        masque = (1 << (7 * longueur)) - 1
        valeur &= masque
        if valeur == masque:
            return TAILLE_INCONNUE, longueur
    return valeur, longueur


def lire_exact(f, n):
    """n octets, quitte à s'y reprendre : un partage réseau rend ce qu'il veut."""
    morceaux, reste = [], n
    while reste > 0:
        bloc = f.read(min(reste, MORCEAU))
        if not bloc:
            break
        morceaux.append(bloc)
        reste -= len(bloc)
    return b"".join(morceaux)


def lire_entete(f, position):
    """(identifiant, taille, longueur de l'en-tête, octets lus) à cette position.

    Les octets lus sont rendus avec le reste : ils mordent déjà sur le contenu, et les relire coûterait un aller-retour de plus. Le saut n'est fait que s'il est nécessaire, pour qu'un parcours séquentiel le reste vraiment.
    """
    if f.tell() != position:
        f.seek(position)
    tampon = lire_exact(f, MAX_ENTETE)
    ident, longueur = lire_vint(tampon, 0, True)
    if ident is None:
        return None, None, 0, tampon
    taille, suite = lire_vint(tampon, longueur, False)
    if taille is None:
        return ident, None, 0, tampon
    return ident, taille, longueur + suite, tampon


def _elements(bloc, debut=0, fin=None):
    """Énumère (identifiant, position du contenu, taille) entre début et fin.

    S'arrête dès que la chaîne ne se lit plus, sans rien signaler : ce parcours sert à extraire des valeurs d'un index, pas à juger le fichier.
    """
    fin = len(bloc) if fin is None else fin
    j = debut
    while j < fin:
        ident, longueur = lire_vint(bloc, j, True)
        if ident is None:
            return
        taille, suite = lire_vint(bloc, j + longueur, False)
        if taille is None or taille < 0 or j + longueur + suite + taille > fin:
            return
        contenu = j + longueur + suite
        yield ident, contenu, taille
        j = contenu + taille


def _entier(bloc, contenu, taille, maximum=8):
    """Entier non signé posé là, ou None si sa taille n'a rien de vraisemblable."""
    if not 0 < taille <= maximum:
        return None
    return int.from_bytes(bloc[contenu:contenu + taille], "big")


def reperes_seekhead(bloc, debut):
    """[(identifiant visé, position absolue)] annoncés par l'index SeekHead.

    L'index donne des positions RELATIVES au début du contenu du Segment ; on les rend absolues tout de suite, seule forme utilisable ensuite. Chaque entrée Seek porte un SeekID et un SeekPosition : il faut suivre l'imbrication pour les lire, car chercher ces deux octets au fil du bloc les trouverait aussi au hasard des données.
    """
    couples = []
    for ident, contenu, taille in _elements(bloc):
        if ident != SEEK:
            continue
        vise = position = None
        for sous, ou, combien in _elements(bloc, contenu, contenu + taille):
            if sous == SEEK_ID:
                vise = _entier(bloc, ou, combien, maximum=4)
            elif sous == SEEK_POSITION:
                position = _entier(bloc, ou, combien)
        if vise is not None and position is not None:
            couples.append((vise, debut + position))
    return couples


def clusters_de_cues(bloc, debut):
    """Positions absolues des clusters, telles que la table Cues les annonce.

    C'est le seul moyen d'atteindre le dernier cluster sans parcourir tous les autres - donc de contrôler la queue du fichier pour le prix d'une lecture. Trois niveaux à descendre : CuePoint, CueTrackPositions, CueClusterPosition.
    """
    positions = []
    for ident, contenu, taille in _elements(bloc):
        if ident != CUE_POINT:
            continue
        for sous, ou, combien in _elements(bloc, contenu, contenu + taille):
            if sous != CUE_TRACK_POSITIONS:
                continue
            for feuille, la, longueur in _elements(bloc, ou, ou + combien):
                if feuille != CUE_CLUSTER_POSITION:
                    continue
                valeur = _entier(bloc, la, longueur)
                if valeur is not None:
                    positions.append(debut + valeur)
    return positions


def _segment(f, taille_fichier):
    """(début du contenu du Segment, fin annoncée, défaut éventuel).

    La taille annoncée par le Segment doit tomber sur la fin exacte du fichier : c'est le contrôle le plus large qui soit, et il coûte deux lectures.
    """
    ident, taille, entete, _ = lire_entete(f, 0)
    if ident != ENTETE_EBML:
        return 0, 0, Defaut("ce n'est pas un fichier Matroska : pas d'entete EBML", 0)
    if taille is None or taille < 0:
        return 0, 0, Defaut("l'entete EBML n'annonce pas de taille lisible", 0)
    position = entete + taille
    ident, taille, entete, _ = lire_entete(f, position)
    if ident != SEGMENT:
        return 0, 0, Defaut("aucun Segment apres l'entete EBML", position)
    debut = position + entete
    if taille == TAILLE_INCONNUE:
        return debut, taille_fichier, None      # légitime : flux sans taille annoncée
    fin = debut + taille
    if fin != taille_fichier:
        ecart = taille_fichier - fin
        ou = "en trop apres la fin" if ecart > 0 else "manquants avant la fin"
        return debut, fin, Defaut(f"le Segment annonce {taille} octets de contenu : {abs(ecart)} octets {ou}", debut)
    return debut, fin, None


def _dans_cluster(corps, base):
    """Suit la chaîne interne d'un cluster : horodatage, puis blocs bout à bout."""
    j = 0
    while j < len(corps):
        ident, longueur = lire_vint(corps, j, True)
        if ident is None or ident not in ENFANTS_CLUSTER:
            return Defaut("contenu de cluster imprevu", base + j)
        taille, suite = lire_vint(corps, j + longueur, False)
        if taille is None or taille < 0:
            return Defaut("bloc sans taille lisible", base + j)
        fin = j + longueur + suite + taille
        if fin > len(corps):
            return Defaut(f"un bloc deborde de son cluster de {fin - len(corps)} octets", base + j)
        j = fin
    return None


def _chaine(f, depart, fin, taille_fichier, complet):
    """Suit les éléments de niveau 1, de départ jusqu'à fin."""
    position = depart
    while position < fin:
        ident, taille, entete, tampon = lire_entete(f, position)
        if ident is None:
            return Defaut("element illisible", position)
        if ident not in NIVEAU1:
            return Defaut(f"identifiant {ident:#x} inconnu a ce niveau", position)
        if taille is None:
            return Defaut(f"l'element {NIVEAU1[ident]} n'annonce pas de taille lisible", position)
        if taille == TAILLE_INCONNUE:
            return Defaut(f"l'element {NIVEAU1[ident]} n'annonce pas sa taille", position)
        suivant = position + entete + taille
        if suivant > taille_fichier:
            return Defaut(f"l'element {NIVEAU1[ident]} deborde de la fin du fichier "
                          f"de {suivant - taille_fichier} octets", position)
        if complet and ident == CLUSTER:
            # Les octets déjà lus pour l'en-tête mordent sur le contenu : on les garde et on lit la suite d'affilée, sans revenir en arrière.
            deja = tampon[entete:entete + taille]
            corps = deja + lire_exact(f, taille - len(deja))
            defaut = _dans_cluster(corps, position + entete)
            if defaut:
                return defaut
        position = suivant
    if position != fin:
        return Defaut(f"le dernier element depasse la fin du Segment de "
                      f"{position - fin} octets", fin)
    return None


def _rapide(f, debut, fin, taille_fichier):
    """Contrôle par points de repère, à prix fixe quelle que soit la taille.

    On demande à l'index où il a rangé les grands éléments, et on va vérifier qu'ils y sont ; puis on remonte la queue depuis le dernier cluster indexe. C'est la que se logent les dégâts observés en pratique - une fin de fichier écrite à moitié.
    """
    # L'index ouvre le Segment chez mkvmerge, mais d'autres muxeurs glissent un Void devant : on regarde les premiers éléments plutôt que le premier seul.
    position, index = debut, None
    for _ in range(4):
        ident, taille, entete, _ = lire_entete(f, position)
        if ident is None:
            return Defaut("element illisible en tete du Segment", position)
        if ident == SEEK_HEAD:
            index = (position + entete, taille)
            break
        if ident not in NIVEAU1 or taille is None or taille < 0:
            return Defaut("le Segment ne commence pas par un element connu", position)
        position += entete + taille
    if index is None or not 0 < index[1] < MAX_INDEX:
        return None                       # sans index, la queue reste hors de portée
    f.seek(index[0])
    couples = reperes_seekhead(lire_exact(f, index[1]), debut)

    for vise, position in couples:
        if vise not in NIVEAU1:
            continue                      # repère vers un élément qu'on ne juge pas
        if not debut <= position < taille_fichier:
            return Defaut(f"l'index annonce {NIVEAU1[vise]} hors du fichier", position)
        trouve = lire_entete(f, position)[0]
        if trouve != vise:
            return Defaut(f"l'index annonce {NIVEAU1[vise]} ici, on y trouve "
                          f"{NIVEAU1.get(trouve, 'des donnees')}", position)

    position_cues = next((p for i, p in couples if i == CUES), None)
    if position_cues is None:
        return None
    ident, taille, entete, _ = lire_entete(f, position_cues)
    if ident != CUES or taille is None or taille < 0:
        return Defaut("table Cues illisible", position_cues)
    f.seek(position_cues + entete)
    clusters = clusters_de_cues(lire_exact(f, taille), debut)
    if not clusters:
        return None
    for position in (min(clusters), max(clusters)):
        if not debut <= position < taille_fichier:
            return Defaut("la table Cues annonce un cluster hors du fichier", position)
        if lire_entete(f, position)[0] != CLUSTER:
            return Defaut("la table Cues annonce un cluster qui n'y est pas", position)
    return _chaine(f, max(clusters), fin, taille_fichier, complet=False)


def verifier(chemin, complet=False):
    """Premier défaut de structure du fichier, ou None s'il est sain.

    On s'arrête au premier : une chaîne rompue rend tout ce qui suit ininterprétable, et énumérer les conséquences d'un même dégât n'apprendrait rien de plus à qui doit décider de remuxer ou non.
    """
    try:
        taille_fichier = os.path.getsize(chemin)
    except OSError as exc:
        return Defaut(f"fichier illisible : {exc.strerror or exc}")
    if taille_fichier == 0:
        return Defaut("fichier vide")
    try:
        with open(chemin, "rb", buffering=0) as f:
            debut, fin, defaut = _segment(f, taille_fichier)
            if defaut:
                return defaut
            if complet:
                return _chaine(f, debut, fin, taille_fichier, complet=True)
            return _rapide(f, debut, fin, taille_fichier)
    except OSError as exc:
        return Defaut(f"lecture interrompue : {exc.strerror or exc}")
