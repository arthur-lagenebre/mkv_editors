"""Lecture et écriture des métadonnées d'un .flac, sans outil externe.

Un .flac commence par "fLaC", puis une suite de blocs "type + taille + contenu", puis le son. Les tags vivent dans le bloc VORBIS_COMMENT (des lignes "CLÉ=valeur"), les images dans des blocs PICTURE, et un bloc PADDING réserve de la place vide derrière eux. C'est lui qui rend l'écriture quasi instantanée : tant que les nouveaux blocs tiennent dans l'espace déjà occupé, on réécrit l'en-tête sur place et le son ne bouge pas d'un octet. Sinon - une pochette ajoutée ne tient jamais dans les 8 Ko que laisse libFLAC - le fichier est recopié à côté, puis substitué à l'original.

Aucun outil de la chaîne FLAC n'est requis : metaflac n'est pas installé avec FFmpeg, et ffmpeg, lui, remultiplexe le fichier entier à chaque retouche.
"""

import os
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"fLaC"
STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, CUESHEET, PICTURE = range(7)
MAX_BLOCK = (1 << 24) - 1     # la taille d'un bloc tient sur 24 bits
FRONT_COVER = 3               # type d'image "couverture (recto)" de la spécification
DEFAULT_PADDING = 8192        # ce que laisse libFLAC : de quoi retoucher les tags plus tard sans tout recopier


class FlacError(Exception):
    """Fichier illisible ou écriture impossible : l'appelant passe au suivant."""


@dataclass
class Picture:
    """Une image embarquée (bloc PICTURE)."""
    kind: int
    mime: str
    data: bytes
    description: str = ""
    width: int = 0
    height: int = 0
    depth: int = 0
    colors: int = 0


@dataclass
class Metadata:
    """Ce que l'en-tête d'un .flac contient, tel qu'on l'a lu."""
    blocks: list                                   # [(type, contenu)] hors PADDING, dans l'ordre du fichier
    audio_offset: int                              # octet où commence le son
    vendor: str = ""
    comments: list = field(default_factory=list)   # [(clé telle qu'écrite, valeur)]
    pictures: list = field(default_factory=list)   # [Picture]
    sample_rate: int = 0
    total_samples: int = 0

    @property
    def duration(self):
        """Durée exacte en secondes, lue dans STREAMINFO. None si le fichier ne la dit pas."""
        if not (self.sample_rate and self.total_samples):
            return None
        return self.total_samples / self.sample_rate

    def values(self, key):
        """Valeurs d'une clé, sans distinction de casse : "Date" et "DATE" sont la même clé."""
        key = key.upper()
        return [value for name, value in self.comments if name.upper() == key]

    def first(self, key):
        values = self.values(key)
        return values[0] if values else ""

    @property
    def has_front_cover(self):
        return any(p.kind == FRONT_COVER for p in self.pictures)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------
def _unpack(fmt, body, offset):
    try:
        return struct.unpack_from(fmt, body, offset)
    except struct.error as e:
        raise FlacError("bloc de metadonnees tronque") from e


def parse_comments(body):
    """(vendor, [(clé, valeur)]) d'un bloc VORBIS_COMMENT. Longueurs en petit-boutiste, contrairement au reste du format."""
    (length,) = _unpack("<I", body, 0)
    vendor = body[4:4 + length].decode("utf-8", "replace")
    position = 4 + length
    (count,) = _unpack("<I", body, position)
    position += 4
    comments = []
    for _ in range(count):
        (length,) = _unpack("<I", body, position)
        position += 4
        if position + length > len(body):
            raise FlacError("commentaire Vorbis deborde de son bloc")
        line = body[position:position + length].decode("utf-8", "replace")
        position += length
        name, sep, value = line.partition("=")
        if sep:                                    # une ligne sans "=" n'est pas un tag : on l'ignore
            comments.append((name, value))
    return vendor, comments


def parse_picture(body):
    """Picture d'un bloc PICTURE. Tout y est en gros-boutiste."""
    kind, length = _unpack(">II", body, 0)
    mime = body[8:8 + length].decode("ascii", "replace")
    position = 8 + length
    (length,) = _unpack(">I", body, position)
    description = body[position + 4:position + 4 + length].decode("utf-8", "replace")
    position += 4 + length
    width, height, depth, colors, length = _unpack(">IIIII", body, position)
    position += 20
    if position + length > len(body):
        raise FlacError("image deborde de son bloc")
    return Picture(kind, mime, body[position:position + length], description, width, height, depth, colors)


def parse_streaminfo(body):
    """(fréquence, nombre d'échantillons) : 20 bits à l'octet 10, puis 3 + 5 bits, puis 36 bits."""
    if len(body) < 18:
        return 0, 0
    sample_rate = (body[10] << 12) | (body[11] << 4) | (body[12] >> 4)
    total = ((body[13] & 0x0F) << 32) | int.from_bytes(body[14:18], "big")
    return sample_rate, total


def read(path):
    """Metadata d'un .flac. Le son n'est pas lu, le padding est sauté."""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            if head != MAGIC:
                cause = " (une etiquette ID3 le precede)" if head[:3] == b"ID3" else ""
                raise FlacError("ce n'est pas un fichier FLAC" + cause)
            blocks, offset = [], 4
            while True:
                header = f.read(4)
                if len(header) < 4:
                    raise FlacError("en-tete de bloc tronque")
                last, kind, size = header[0] & 0x80, header[0] & 0x7F, int.from_bytes(header[1:], "big")
                offset += 4 + size
                if kind == PADDING:
                    f.seek(size, os.SEEK_CUR)
                else:
                    body = f.read(size)
                    if len(body) < size:
                        raise FlacError("bloc de metadonnees tronque")
                    blocks.append((kind, body))
                if last:
                    break
    except OSError as e:
        raise FlacError(f"lecture impossible : {e.strerror or e}") from e

    if not blocks or blocks[0][0] != STREAMINFO:
        raise FlacError("STREAMINFO absent en tete du fichier")
    meta = Metadata(blocks, offset)
    meta.sample_rate, meta.total_samples = parse_streaminfo(blocks[0][1])
    for kind, body in blocks:
        if kind == VORBIS_COMMENT:
            meta.vendor, meta.comments = parse_comments(body)
        elif kind == PICTURE:
            meta.pictures.append(parse_picture(body))
    return meta


# --------------------------------------------------------------------------
# Écriture
# --------------------------------------------------------------------------
def build_comments(vendor, comments):
    lines = [f"{name}={value}".encode("utf-8") for name, value in comments]
    vendor = vendor.encode("utf-8")
    parts = [struct.pack("<I", len(vendor)), vendor, struct.pack("<I", len(lines))]
    for line in lines:
        parts += [struct.pack("<I", len(line)), line]
    return b"".join(parts)


def build_picture(picture):
    mime, description = picture.mime.encode("ascii"), picture.description.encode("utf-8")
    return b"".join([struct.pack(">II", picture.kind, len(mime)), mime, struct.pack(">I", len(description)), description, struct.pack(">IIIII", picture.width, picture.height, picture.depth, picture.colors, len(picture.data)), picture.data])


def image_dimensions(data):
    """(largeur, hauteur, bits par pixel) d'un JPEG ou d'un PNG, zéros si on ne sait pas.

    La spécification demande ces valeurs ; certains lecteurs s'en servent pour choisir l'image à afficher.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 26:
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(data[25], 1)
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"), data[24] * channels
    if data[:2] == b"\xff\xd8":
        position = 2
        while position + 9 < len(data):
            if data[position] != 0xFF:
                break
            marker = data[position + 1]
            if marker == 0xFF:                     # octet de bourrage entre deux marqueurs
                position += 1
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):   # SOF : là où la taille est écrite
                height = int.from_bytes(data[position + 5:position + 7], "big")
                width = int.from_bytes(data[position + 7:position + 9], "big")
                return width, height, data[position + 4] * data[position + 9]
            position += 2 + int.from_bytes(data[position + 2:position + 4], "big")
    return 0, 0, 0


def front_cover(data, mime="image/jpeg"):
    """Picture de couverture pour ces octets, dimensions comprises."""
    width, height, depth = image_dimensions(data)
    return Picture(FRONT_COVER, mime, data, "", width, height, depth, 0)


def _block(kind, body, last):
    if len(body) > MAX_BLOCK:
        raise FlacError(f"bloc de {len(body)} octets : un bloc FLAC ne depasse pas 16 Mo")
    return bytes([kind | (0x80 if last else 0)]) + len(body).to_bytes(3, "big") + body


def render(meta, comments, pictures, padding):
    """L'en-tête complet : STREAMINFO d'abord, les blocs qu'on ne touche pas ensuite, puis les tags, les images et le padding."""
    kept = [(kind, body) for kind, body in meta.blocks if kind not in (VORBIS_COMMENT, PICTURE)]
    blocks = kept + [(VORBIS_COMMENT, build_comments(meta.vendor, comments))] + [(PICTURE, build_picture(p)) for p in pictures]
    if padding is not None:
        blocks.append((PADDING, bytes(padding)))
    return MAGIC + b"".join(_block(kind, body, i == len(blocks) - 1) for i, (kind, body) in enumerate(blocks))


def write(path, meta, comments, pictures):
    """Remplace les tags et les images d'un .flac. Retourne "sur place" ou "recopie".

    `meta` doit être la lecture du fichier tel qu'il est : c'est elle qui dit où commence le son. Sur place, seul l'en-tête est réécrit, à taille identique - le padding absorbe la différence. Sinon le fichier est recopié à côté avec un padding neuf, sa taille contrôlée, et il ne remplace l'original qu'une fois complet : une coupure en route laisse l'original intact.
    """
    path = Path(path)
    bare = len(render(meta, comments, pictures, None))
    room = meta.audio_offset - bare
    if room == 0 or room >= 4:                     # un bloc PADDING coûte au moins son en-tête de 4 octets
        header = render(meta, comments, pictures, room - 4 if room else None)
        try:
            with open(path, "r+b") as f:
                f.write(header)
        except OSError as e:
            raise FlacError(f"ecriture impossible : {e.strerror or e}") from e
        return "sur place"

    header = render(meta, comments, pictures, DEFAULT_PADDING)
    temporary = None
    try:
        size = path.stat().st_size
        with open(path, "rb") as source, tempfile.NamedTemporaryFile(dir=path.parent, prefix=".", suffix=".flac.tmp", delete=False) as target:
            temporary = Path(target.name)
            target.write(header)
            source.seek(meta.audio_offset)
            shutil.copyfileobj(source, target, 1 << 20)
            target.flush()
            os.fsync(target.fileno())
        expected = len(header) + size - meta.audio_offset
        if temporary.stat().st_size != expected:
            raise FlacError("copie incomplete : l'original est garde")
        os.replace(temporary, path)
        temporary = None
    except OSError as e:
        raise FlacError(f"ecriture impossible : {e.strerror or e}") from e
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return "recopie"
