"""Contrôle de structure d'un .mkv (aucun outil externe requis).

Les fichiers d'essai sont fabriqués octet par octet : c'est le seul moyen d'abîmer une structure exactement là où on le veut, et de vérifier que le contrôle rapide et le contrôle complet ne voient pas les mêmes choses.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import ebml


# ---------------------------------------------------------------------------
# Fabrication d'un .mkv minimal mais licite
# ---------------------------------------------------------------------------
def octets_id(ident):
    """Un identifiant s'écrit tel quel : ses bits de longueur en font partie."""
    return ident.to_bytes((ident.bit_length() + 7) // 8, "big")


def taille_vint(n, longueur=None):
    """Taille d'un élément, marquée de sa propre longueur."""
    if longueur is None:
        longueur = next(l for l in range(1, 9) if n < (1 << (7 * l)) - 1)
    return ((1 << (7 * longueur)) | n).to_bytes(longueur, "big")


def element(ident, contenu):
    return octets_id(ident) + taille_vint(len(contenu)) + contenu


def index(reperes):
    """SeekHead : positions sur 8 octets fixes, pour que sa longueur ne bouge pas."""
    entrees = b"".join(element(ebml.SEEK, element(ebml.SEEK_ID, octets_id(vise)) + element(ebml.SEEK_POSITION, position.to_bytes(8, "big"))) for vise, position in reperes)
    return element(ebml.SEEK_HEAD, entrees)


def table_cues(positions):
    points = b"".join(
        element(ebml.CUE_POINT,
                element(0xB3, bytes([n + 1]))                       # CueTime
                + element(ebml.CUE_TRACK_POSITIONS,
                          element(0xF7, b"\x01")                    # CueTrack
                          + element(ebml.CUE_CLUSTER_POSITION, p.to_bytes(8, "big"))))
        for n, p in enumerate(positions))
    return element(ebml.CUES, points)


def construire(nb_clusters=3, taille_bloc=40):
    """(octets du fichier, offsets utiles). Un film réduit à son squelette."""
    entete = element(ebml.ENTETE_EBML, element(0x4282, b"matroska"))
    info = element(0x1549A966, element(0x2AD7B1, (1000000).to_bytes(4, "big")))
    pistes = element(0x1654AE6B, element(0xAE, b""))
    clusters = [element(ebml.CLUSTER, element(0xE7, bytes([n + 1])) + element(0xA3, b"\x81\x00\x00\x80" + b"x" * taille_bloc)) for n in range(nb_clusters)]

    # L'index cite des positions qui dépendent de sa propre longueur : on la mesure d'abord sur un index bidon, de même forme donc de même taille.
    longueur_index = len(index([(0x1549A966, 0), (0x1654AE6B, 0), (ebml.CUES, 0)]))
    position = longueur_index
    offsets = {"info": position}
    position += len(info)
    offsets["pistes"] = position
    position += len(pistes)
    offsets["clusters"] = []
    for cluster in clusters:
        offsets["clusters"].append(position)
        position += len(cluster)
    offsets["cues"] = position

    cues = table_cues(offsets["clusters"])
    vrai_index = index([(0x1549A966, offsets["info"]), (0x1654AE6B, offsets["pistes"]), (ebml.CUES, offsets["cues"])])
    assert len(vrai_index) == longueur_index
    contenu = vrai_index + info + pistes + b"".join(clusters) + cues
    segment = element(ebml.SEGMENT, contenu)
    debut = len(entete) + (len(segment) - len(contenu))
    offsets = {cle: ([debut + p for p in valeur] if isinstance(valeur, list) else debut + valeur) for cle, valeur in offsets.items()}
    offsets["debut"] = debut
    return entete + segment, offsets


def verifier(donnees, complet=False):
    """Écrit les octets dans un fichier temporaire et rend le défaut trouvé."""
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "essai.mkv"
        chemin.write_bytes(donnees)
        return ebml.verifier(chemin, complet=complet)


# ---------------------------------------------------------------------------
class TestEntiersATailleVariable(unittest.TestCase):
    def test_identifiant_garde_ses_bits_de_longueur(self):
        bloc = octets_id(ebml.SEGMENT)
        self.assertEqual(ebml.lire_vint(bloc, 0, True), (ebml.SEGMENT, 4))

    def test_taille_perd_ses_bits_de_longueur(self):
        self.assertEqual(ebml.lire_vint(taille_vint(300), 0, False)[0], 300)

    def test_taille_inconnue(self):
        # Tous les bits à 1 : légitime pour un flux en direct, pas pour un film.
        self.assertEqual(ebml.lire_vint(b"\xff", 0, False)[0], ebml.TAILLE_INCONNUE)

    def test_premier_octet_nul_ne_code_rien(self):
        self.assertEqual(ebml.lire_vint(b"\x00\x01", 0, True), (None, 0))

    def test_bloc_trop_court(self):
        self.assertEqual(ebml.lire_vint(b"\x40", 0, False), (None, 0))


class TestLectureDesIndex(unittest.TestCase):
    def test_reperes_rendus_en_positions_absolues(self):
        # L'index donne des positions relatives au Segment ; on les veut absolues.
        contenu = index([(ebml.CUES, 500)])[5:]             # sans son en-tête
        self.assertEqual(ebml.reperes_seekhead(contenu, 52), [(ebml.CUES, 552)])

    def test_positions_de_clusters(self):
        contenu = table_cues([100, 200])[5:]
        self.assertEqual(ebml.clusters_de_cues(contenu, 52), [152, 252])

    def test_un_octet_f1_perdu_dans_les_donnees_ne_compte_pas(self):
        # Le balayage naïf prenait ces octets pour des positions de cluster.
        self.assertEqual(ebml.clusters_de_cues(b"\xf1\xf1\xf1\xf1", 0), [])


class TestFichierSain(unittest.TestCase):
    def test_rien_a_signaler(self):
        donnees, _ = construire()
        self.assertIsNone(verifier(donnees))
        self.assertIsNone(verifier(donnees, complet=True))

    def test_un_seul_cluster(self):
        donnees, _ = construire(nb_clusters=1)
        self.assertIsNone(verifier(donnees, complet=True))


class TestDegatsVisiblesEnRapide(unittest.TestCase):
    """Ce qui se voit sans lire le fichier entier."""

    def test_fichier_vide(self):
        self.assertIn("vide", str(verifier(b"")))

    def test_pas_matroska(self):
        self.assertIn("pas d'entete EBML", str(verifier(b"ceci n'est pas un film")))

    def test_troncature(self):
        donnees, _ = construire()
        defaut = verifier(donnees[:-30])
        self.assertIn("30 octets manquants", str(defaut))

    def test_octets_en_trop_a_la_fin(self):
        donnees, _ = construire()
        defaut = verifier(donnees + b"\x00" * 12)
        self.assertIn("12 octets en trop", str(defaut))

    def test_index_qui_ment(self):
        # L'index annonce la table Cues là où se trouve en fait l'en-tête Info.
        donnees, offsets = construire()
        faux = bytearray(donnees)
        relative = (offsets["cues"] - offsets["debut"]).to_bytes(8, "big")
        cible = donnees.index(relative)
        faux[cible:cible + 8] = (offsets["info"] - offsets["debut"]).to_bytes(8, "big")
        self.assertIn("l'index annonce Cues ici", str(verifier(bytes(faux))))

    def test_queue_rompue(self):
        # La taille annoncée du dernier cluster fait retomber la chaîne ailleurs : l'octet 4 du cluster est sa taille (4 octets d'identifiant avant elle).
        donnees, offsets = construire()
        faux = bytearray(donnees)
        dernier = offsets["clusters"][-1]
        faux[dernier + 4] = (faux[dernier + 4] + 1) % 256
        self.assertIsNotNone(verifier(bytes(faux)))


class TestDegatsVisiblesEnCompletSeulement(unittest.TestCase):
    """Un dégât au milieu du film ne se voit qu'en suivant toute la chaîne."""

    def test_bloc_qui_deborde_de_son_cluster(self):
        donnees, offsets = construire(nb_clusters=5)
        faux = bytearray(donnees)
        milieu = offsets["clusters"][2]
        # On allonge le SimpleBlock au-delà de ce que le cluster contient.
        interieur = donnees.index(b"\x81\x00\x00\x80", milieu)
        faux[interieur - 1] = (faux[interieur - 1] + 20) % 256
        self.assertIsNone(verifier(bytes(faux)))
        self.assertIn("cluster", str(verifier(bytes(faux), complet=True)))

    def test_contenu_de_cluster_imprevu(self):
        donnees, offsets = construire(nb_clusters=5)
        faux = bytearray(donnees)
        interieur = donnees.index(b"\x81\x00\x00\x80", offsets["clusters"][2])
        faux[interieur - 2] = 0x77                    # identifiant qui n'existe pas la
        self.assertIsNone(verifier(bytes(faux)))
        self.assertIsNotNone(verifier(bytes(faux), complet=True))


class TestFichierInaccessible(unittest.TestCase):
    def test_chemin_absent(self):
        defaut = ebml.verifier(Path("nulle-part") / "rien.mkv")
        self.assertIn("illisible", str(defaut))


if __name__ == "__main__":
    unittest.main()
