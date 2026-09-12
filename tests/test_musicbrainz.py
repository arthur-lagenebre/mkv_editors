"""Client MusicBrainz : forme des requêtes, cadence, réessais, pochettes.

Aucun appel réseau : urlopen est remplacé par un double qui rejoue une file de réponses préparées, et l'horloge est figée pour mesurer les attentes sans les subir.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import cache
from musiclib import musicbrainz as mb_mod
from musiclib.musicbrainz import MusicBrainz, MusicBrainzError
from tests.test_tmdb import FakeResponse, http_error


class ClientTestCase(unittest.TestCase):
    def call(self, reponses, methode="get", args=("release/x",), **kwargs):
        """Joue `reponses` et retourne (résultat, requêtes, attentes demandées)."""
        requetes, attentes = [], []

        def fake_urlopen(request, timeout=None):
            requetes.append(request)
            item = reponses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        client = kwargs.pop("client", None) or MusicBrainz(**kwargs)
        with mock.patch.object(mb_mod, "urlopen", fake_urlopen), mock.patch.object(mb_mod.time, "sleep", attentes.append), mock.patch.object(mb_mod.time, "monotonic", lambda: 100.0):
            return getattr(client, methode)(*args), requetes, attentes


class TestRequetes(ClientTestCase):
    def test_user_agent_et_format_json(self):
        _, reqs, _ = self.call([FakeResponse(b"{}")])
        self.assertIn("mkv_editors", reqs[0].headers["User-agent"])
        self.assertTrue(reqs[0].full_url.endswith("release/x?fmt=json"))

    def test_format_ajoute_apres_une_requete_existante(self):
        _, reqs, _ = self.call([FakeResponse(b"{}")], args=("release/x?inc=genres",))
        self.assertIn("?inc=genres&fmt=json", reqs[0].full_url)

    def test_recherche_exacte(self):
        _, reqs, _ = self.call([FakeResponse(b'{"releases": []}')], methode="search_releases", args=("Born Dead", "Body Count"))
        self.assertIn('query=release:"Born Dead" AND artist:"Body Count"', unquote(reqs[0].full_url))

    def test_guillemets_neutralises_dans_l_expression(self):
        _, reqs, _ = self.call([FakeResponse(b'{"releases": []}')], methode="search_releases", args=('Le "grand" album',))
        self.assertIn('release:"Le  grand  album"', unquote(reqs[0].full_url))

    def test_recherche_large_echappe_la_syntaxe(self):
        # "TRON: Legacy" chercherait sinon un champ nommé TRON.
        _, reqs, _ = self.call([FakeResponse(b'{"releases": []}')], methode="search_releases", args=("TRON: Legacy", None, False))
        self.assertIn(r"release:(TRON\: Legacy)", unquote(reqs[0].full_url))

    def test_resultats_rendus(self):
        data, _, _ = self.call([FakeResponse(b'{"releases": [{"id": "a"}]}')], methode="search_releases", args=("A",))
        self.assertEqual(data, [{"id": "a"}])


class TestCadence(ClientTestCase):
    def test_deux_requetes_espacees(self):
        client = MusicBrainz(interval=1.1)
        self.call([FakeResponse(b"{}")], client=client)
        _, _, attentes = self.call([FakeResponse(b"{}")], client=client)
        self.assertEqual(len(attentes), 1)
        self.assertAlmostEqual(attentes[0], 1.1)

    def test_premiere_requete_sans_attente(self):
        _, _, attentes = self.call([FakeResponse(b"{}")])
        self.assertEqual(attentes, [])

    def test_le_cache_ne_coute_aucune_attente(self):
        with tempfile.TemporaryDirectory() as d:
            client = MusicBrainz(cache=cache.Cache(Path(d)))
            self.call([FakeResponse(b'{"id": 1}')], client=client)
            data, reqs, attentes = self.call([], client=client)
        self.assertEqual((data, reqs, attentes), ({"id": 1}, [], []))


class TestErreurs(ClientTestCase):
    def test_503_reessaye(self):
        data, reqs, _ = self.call([http_error(503, {"Retry-After": "2"}), FakeResponse(b'{"id": 7}')])
        self.assertEqual((data, len(reqs)), ({"id": 7}, 2))

    def test_connexion_coupee_reessayee(self):
        # Mesuré : depuis urllib, une connexion sur cinq est coupée en pleine négociation TLS.
        data, reqs, _ = self.call([URLError(ConnectionResetError(10054, "coupee")), FakeResponse(b'{"id": 7}')])
        self.assertEqual((data, len(reqs)), ({"id": 7}, 2))

    def test_panne_persistante_abandonnee(self):
        with self.assertRaises(MusicBrainzError):
            self.call([URLError("coupure")] * 2, attempts=2)

    def test_404_n_est_pas_reessaye(self):
        reponses = [http_error(404), FakeResponse(b"{}")]
        with self.assertRaisesRegex(MusicBrainzError, "404"):
            self.call(reponses)
        self.assertEqual(len(reponses), 1)

    def test_requete_refusee_n_est_pas_reessayee(self):
        reponses = [http_error(400), FakeResponse(b"{}")]
        with self.assertRaises(MusicBrainzError):
            self.call(reponses)
        self.assertEqual(len(reponses), 1)

    def test_json_illisible(self):
        with self.assertRaises(MusicBrainzError):
            self.call([FakeResponse(b"<html>maintenance</html>")])


class TestPochettes(ClientTestCase):
    def test_pochette_de_la_sortie(self):
        data, reqs, attentes = self.call([FakeResponse(b"JPEG", {"Content-Length": "4"})], methode="cover", args=("rel", "grp"))
        self.assertEqual(data, b"JPEG")
        self.assertTrue(reqs[0].full_url.endswith("/release/rel/front-1200"))
        self.assertEqual(attentes, [])                  # Cover Art Archive n'impose pas de cadence

    def test_repli_sur_le_release_group(self):
        data, reqs, _ = self.call([http_error(404), FakeResponse(b"JPEG")], methode="cover", args=("rel", "grp", "500"))
        self.assertEqual(data, b"JPEG")
        self.assertTrue(reqs[1].full_url.endswith("/release-group/grp/front-500"))

    def test_release_group_seul(self):
        # La fiche dit déjà que l'édition n'a pas d'image : inutile de demander un 404.
        data, reqs, _ = self.call([FakeResponse(b"JPEG")], methode="cover", args=(None, "grp"))
        self.assertEqual((data, len(reqs)), (b"JPEG", 1))
        self.assertTrue(reqs[0].full_url.endswith("/release-group/grp/front-1200"))

    def test_aucune_pochette(self):
        data, _, _ = self.call([http_error(404), http_error(404)], methode="cover", args=("rel", "grp"))
        self.assertIsNone(data)

    def test_taille_originale(self):
        _, reqs, _ = self.call([FakeResponse(b"JPEG")], methode="cover", args=("rel", None, "original"))
        self.assertTrue(reqs[0].full_url.endswith("/release/rel/front"))

    def test_telechargement_tronque_detecte(self):
        # Embarquée telle quelle, une image coupée resterait dans chaque piste.
        with self.assertRaisesRegex(MusicBrainzError, "incomplet"):
            self.call([FakeResponse(b"JP", {"Content-Length": "4"})], methode="cover", args=("rel",))


if __name__ == "__main__":
    unittest.main()
