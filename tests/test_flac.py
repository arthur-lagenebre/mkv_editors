"""Métadonnées d'un .flac (aucun outil externe requis).

Les fichiers d'essai sont fabriqués octet par octet, sans passer par le module testé : un écrivain qui se trompe et un lecteur qui se trompe de la même façon se donneraient raison l'un l'autre.
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from musiclib import flac

SON = bytes(range(256)) * 40          # tient lieu de trames audio : seul compte qu'elles ne bougent pas


def bloc(kind, body, last=False):
    return bytes([kind | (0x80 if last else 0)]) + len(body).to_bytes(3, "big") + body


def streaminfo(sample_rate=44100, total=441000):
    body = bytearray(34)
    body[10] = (sample_rate >> 12) & 0xFF
    body[11] = (sample_rate >> 4) & 0xFF
    body[12] = ((sample_rate & 0x0F) << 4) | (1 << 1)        # stéréo
    body[13] = (15 << 4) | ((total >> 32) & 0x0F)            # 16 bits
    body[14:18] = (total & 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(body)


def commentaires(lignes, vendor="reference libFLAC 1.3.3"):
    parts = [struct.pack("<I", len(vendor)), vendor.encode(), struct.pack("<I", len(lignes))]
    for ligne in lignes:
        ligne = ligne.encode("utf-8")
        parts += [struct.pack("<I", len(ligne)), ligne]
    return b"".join(parts)


def image(data, kind=3, mime="image/jpeg"):
    return struct.pack(">II", kind, len(mime)) + mime.encode() + struct.pack(">I", 0) + struct.pack(">IIIII", 500, 500, 24, 0, len(data)) + data


def fichier(lignes=("TITLE=Nightcall", "ARTIST=Kavinsky"), padding=8192, extra=()):
    """Octets d'un .flac : STREAMINFO, blocs supplémentaires, tags, padding, son."""
    blocs = [bloc(flac.STREAMINFO, streaminfo())] + [bloc(k, b) for k, b in extra]
    blocs.append(bloc(flac.VORBIS_COMMENT, commentaires(list(lignes)), last=padding is None))
    if padding is not None:
        blocs.append(bloc(flac.PADDING, bytes(padding), last=True))
    return b"fLaC" + b"".join(blocs) + SON


class FlacTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dossier = Path(self._tmp.name)

    def poser(self, donnees, nom="01 - Nightcall.flac"):
        chemin = self.dossier / nom
        chemin.write_bytes(donnees)
        return chemin


class TestLecture(FlacTestCase):
    def test_tags_et_duree(self):
        meta = flac.read(self.poser(fichier()))
        self.assertEqual(meta.comments, [("TITLE", "Nightcall"), ("ARTIST", "Kavinsky")])
        self.assertEqual(meta.vendor, "reference libFLAC 1.3.3")
        self.assertEqual(meta.duration, 10.0)

    def test_cles_sans_distinction_de_casse(self):
        meta = flac.read(self.poser(fichier(["Date=2010", "GENRE=Electronic", "genre=Synthwave"])))
        self.assertEqual(meta.first("DATE"), "2010")
        self.assertEqual(meta.values("Genre"), ["Electronic", "Synthwave"])

    def test_debut_du_son(self):
        donnees = fichier()
        meta = flac.read(self.poser(donnees))
        self.assertEqual(donnees[meta.audio_offset:], SON)

    def test_image_embarquee(self):
        meta = flac.read(self.poser(fichier(extra=[(flac.PICTURE, image(b"JPEG"))])))
        self.assertTrue(meta.has_front_cover)
        self.assertEqual((meta.pictures[0].data, meta.pictures[0].width), (b"JPEG", 500))

    def test_verso_n_est_pas_une_couverture(self):
        meta = flac.read(self.poser(fichier(extra=[(flac.PICTURE, image(b"JPEG", kind=4))])))
        self.assertFalse(meta.has_front_cover)

    def test_pas_un_flac(self):
        with self.assertRaisesRegex(flac.FlacError, "pas un fichier FLAC"):
            flac.read(self.poser(b"RIFF....WAVE"))

    def test_etiquette_id3_en_tete_signalee(self):
        with self.assertRaisesRegex(flac.FlacError, "ID3"):
            flac.read(self.poser(b"ID3\x04" + fichier()))

    def test_fichier_tronque(self):
        with self.assertRaises(flac.FlacError):
            flac.read(self.poser(fichier()[:60]))

    def test_chemin_absent(self):
        with self.assertRaisesRegex(flac.FlacError, "lecture impossible"):
            flac.read(self.dossier / "absent.flac")


class TestEcriture(FlacTestCase):
    def ecrire(self, donnees, lignes, images=()):
        chemin = self.poser(donnees)
        mode = flac.write(chemin, flac.read(chemin), lignes, list(images))
        return chemin, mode, flac.read(chemin)

    def test_sur_place_quand_le_padding_suffit(self):
        donnees = fichier()
        chemin, mode, relu = self.ecrire(donnees, [("TITLE", "Nightcall"), ("MUSICBRAINZ_ALBUMID", "x" * 36)])
        self.assertEqual(mode, "sur place")
        self.assertEqual(chemin.stat().st_size, len(donnees))       # le padding a absorbé l'ajout
        self.assertEqual(relu.first("MUSICBRAINZ_ALBUMID"), "x" * 36)
        self.assertEqual(chemin.read_bytes()[relu.audio_offset:], SON)

    def test_recopie_quand_ca_ne_tient_pas(self):
        # Une pochette ne tient jamais dans le padding : le fichier est recopié, le son intact.
        chemin, mode, relu = self.ecrire(fichier(padding=16), [("TITLE", "Nightcall")], [flac.front_cover(b"\xff\xd8" + bytes(5000))])
        self.assertEqual(mode, "recopie")
        self.assertTrue(relu.has_front_cover)
        self.assertEqual(chemin.read_bytes()[relu.audio_offset:], SON)
        self.assertEqual(list(self.dossier.glob("*.tmp")), [])      # rien ne traîne à côté

    def test_padding_neuf_apres_recopie(self):
        chemin, _, relu = self.ecrire(fichier(padding=None), [("TITLE", "x" * 100)])
        sans_padding = len(flac.render(relu, relu.comments, relu.pictures, None))
        self.assertEqual(relu.audio_offset - sans_padding, 4 + flac.DEFAULT_PADDING)

    def test_place_exacte_sans_padding(self):
        # Les nouveaux tags occupent pile l'ancien padding et son en-tête.
        donnees = fichier(["TITLE=A"], padding=10)
        chemin, mode, relu = self.ecrire(donnees, [("TITLE", "A" + "b" * 14)])
        self.assertEqual((mode, chemin.stat().st_size), ("sur place", len(donnees)))
        self.assertEqual(relu.first("TITLE"), "A" + "b" * 14)

    def test_trois_octets_de_trop_peu_forcent_la_recopie(self):
        # Moins de 4 octets libres : pas de quoi loger l'en-tête d'un bloc PADDING.
        _, mode, _ = self.ecrire(fichier(["TITLE=A"], padding=10), [("TITLE", "A" + "b" * 12)])
        self.assertEqual(mode, "recopie")

    def test_blocs_etrangers_conserves(self):
        table = bytes(range(18))
        _, _, relu = self.ecrire(fichier(extra=[(flac.SEEKTABLE, table)]), [("TITLE", "Autre")])
        self.assertEqual(relu.blocks[0][0], flac.STREAMINFO)
        self.assertIn((flac.SEEKTABLE, table), relu.blocks)

    def test_accents_et_signe_egal_dans_la_valeur(self):
        _, _, relu = self.ecrire(fichier(), [("TITLE", "Café = crème ℗")])
        self.assertEqual(relu.first("TITLE"), "Café = crème ℗")


class TestDimensions(unittest.TestCase):
    def test_png(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (1200).to_bytes(4, "big") + (800).to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
        self.assertEqual(flac.image_dimensions(png), (1200, 800, 24))

    def test_jpeg(self):
        app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + bytes(14)
        sof = b"\xff\xc0" + (17).to_bytes(2, "big") + bytes([8]) + (500).to_bytes(2, "big") + (487).to_bytes(2, "big") + bytes([3]) + bytes(9)
        self.assertEqual(flac.image_dimensions(b"\xff\xd8" + app0 + sof), (487, 500, 24))

    def test_format_inconnu(self):
        self.assertEqual(flac.image_dimensions(b"GIF89a"), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
