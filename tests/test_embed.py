"""Images encodees dans une fiche HTML : cle, balise, cache, telechargement."""

import base64
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import embed
from mkvlib.tmdb import TmdbError


class FauxTmdb:
    def __init__(self, echecs=()):
        self.echecs = set(echecs)
        self.demandes = []

    def image(self, path, size):
        self.demandes.append((path, size))
        if path in self.echecs:
            raise TmdbError("indisponible")
        return path.encode("utf-8")


class TestCle(unittest.TestCase):
    def test_taille_et_chemin(self):
        self.assertEqual(embed.image_key("/aBc.jpg", "w300"), "w300/aBc.jpg")

    def test_chemin_absent(self):
        self.assertIsNone(embed.image_key(None, "w300"))

    def test_chemin_inattendu_refuse(self):
        # La cle finit dans un attribut HTML : on n'y laisse passer que du connu.
        self.assertIsNone(embed.image_key("/a'onerror=alert(1).jpg", "w300"))


class TestCache(unittest.TestCase):
    def relire(self, html):
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "recap.html"
            page.write_text(html, encoding="utf-8")
            return embed.read_embedded(page)

    def test_relecture_de_ce_qui_a_ete_ecrit(self):
        html = "<div>" + embed.tag("w300/a.jpg", "data:image/jpeg;base64,AAA") + "</div>"
        self.assertEqual(self.relire(html), {"w300/a.jpg": "data:image/jpeg;base64,AAA"})

    def test_ancien_attribut_toujours_lu(self):
        # Les fiches generees avant le partage du code utilisaient 'data-still'.
        html = "<img data-still='w300/b.jpg' src='data:image/jpeg;base64,BBB'>"
        self.assertEqual(self.relire(html), {"w300/b.jpg": "data:image/jpeg;base64,BBB"})

    def test_fiche_absente(self):
        self.assertEqual(embed.read_embedded(Path("nexiste_pas.html")), {})


class TestTelechargement(unittest.TestCase):
    def fetch(self, needed, cached, tmdb):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            images = embed.fetch(needed, cached, "w300", tmdb)
        return images, sortie.getvalue()

    def test_telechargement_et_encodage(self):
        tmdb = FauxTmdb()
        images, _ = self.fetch({"w300/a.jpg": "/a.jpg"}, {}, tmdb)
        attendu = "data:image/jpeg;base64," + base64.b64encode(b"/a.jpg").decode()
        self.assertEqual(images, {"w300/a.jpg": attendu})
        self.assertEqual(tmdb.demandes, [("/a.jpg", "w300")])

    def test_cache_evite_le_reseau(self):
        tmdb = FauxTmdb()
        images, sortie = self.fetch({"w300/a.jpg": "/a.jpg"},
                                    {"w300/a.jpg": "data:image/jpeg;base64,DEJA"}, tmdb)
        self.assertEqual(images["w300/a.jpg"], "data:image/jpeg;base64,DEJA")
        self.assertEqual(tmdb.demandes, [])
        self.assertIn("reprise(s)", sortie)

    def test_image_en_echec_simplement_absente(self):
        tmdb = FauxTmdb(echecs={"/b.jpg"})
        images, sortie = self.fetch({"w300/a.jpg": "/a.jpg", "w300/b.jpg": "/b.jpg"}, {}, tmdb)
        self.assertEqual(list(images), ["w300/a.jpg"])
        self.assertIn("ignoree", sortie)


if __name__ == "__main__":
    unittest.main()
