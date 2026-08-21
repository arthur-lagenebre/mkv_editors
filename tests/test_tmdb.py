"""Client TMDB : forme des requetes, classement des erreurs, reessais.

Aucun appel reseau : urlopen est remplace par un double qui rejoue une file de
reponses preparees.
"""

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import cache
from mkvlib import tmdb as tmdb_mod
from mkvlib.tmdb import Tmdb, TmdbAuthError, TmdbError, release_region


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def http_error(code, headers=None):
    return HTTPError("http://x", code, "erreur", headers or {}, None)


class ClientTestCase(unittest.TestCase):
    def call(self, reponses, methode="get", args=("movie/1",), **kwargs):
        """Joue `reponses` (reponse ou exception) et retourne (resultat, requetes)."""
        requetes = []

        def fake_urlopen(request, timeout=None):
            requetes.append(request)
            item = reponses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        client = Tmdb(kwargs.pop("key", "0123456789abcdef"), **kwargs)
        with mock.patch.object(tmdb_mod, "urlopen", fake_urlopen), \
             mock.patch.object(tmdb_mod.time, "sleep", lambda *_: None):
            return getattr(client, methode)(*args), requetes


class TestRequetes(ClientTestCase):
    def test_cle_v3_en_parametre(self):
        _, reqs = self.call([FakeResponse(b'{"id": 1}')])
        self.assertIn("api_key=0123456789abcdef", reqs[0].full_url)
        self.assertIn("language=fr-FR", reqs[0].full_url)
        self.assertNotIn("Authorization", reqs[0].headers)

    def test_token_v4_en_bearer(self):
        jeton = "ey" + "J0eXAiOiJKV1QifQ.charge.signature"
        _, reqs = self.call([FakeResponse(b"{}")], key=jeton)
        self.assertEqual(reqs[0].headers["Authorization"], f"Bearer {jeton}")
        self.assertNotIn("api_key", reqs[0].full_url)

    def test_parametre_ajoute_apres_une_requete_existante(self):
        _, reqs = self.call([FakeResponse(b"{}")], args=("search/movie?query=Dune",))
        self.assertIn("query=Dune&api_key=", reqs[0].full_url)

    def test_recherche_de_serie(self):
        _, reqs = self.call([FakeResponse(b'{"results": []}')],
                            methode="search_tv", args=("Fargo",))
        self.assertIn("search/tv?query=Fargo", reqs[0].full_url)
        self.assertNotIn("first_air_date_year", reqs[0].full_url)

    def test_recherche_de_serie_avec_annee(self):
        _, reqs = self.call([FakeResponse(b'{"results": []}')],
                            methode="search_tv", args=("Fargo", "2014"))
        self.assertIn("first_air_date_year=2014", reqs[0].full_url)

    def test_titre_encode_dans_l_url(self):
        _, reqs = self.call([FakeResponse(b'{"results": []}')],
                            methode="search_tv", args=("Cowboy Bebop",))
        self.assertIn("query=Cowboy%20Bebop", reqs[0].full_url)

    def test_langue_ponctuelle(self):
        _, reqs = self.call([FakeResponse(b"{}")], methode="series", args=(1, "en-US"))
        self.assertIn("language=en-US", reqs[0].full_url)


class TestErreurs(ClientTestCase):
    def test_cle_refusee_arrete_tout(self):
        with self.assertRaises(TmdbAuthError):
            self.call([http_error(401)])

    def test_introuvable_est_recuperable(self):
        with self.assertRaises(TmdbError):
            self.call([http_error(404)])

    def test_404_n_est_pas_reessaye(self):
        reponses = [http_error(404), FakeResponse(b"{}")]
        with self.assertRaises(TmdbError):
            self.call(reponses)
        self.assertEqual(len(reponses), 1)      # la 2e reponse n'a pas ete consommee

    def test_429_est_reessaye(self):
        (data, reqs) = self.call([http_error(429, {"Retry-After": "1"}),
                                  FakeResponse(b'{"id": 7}')])
        self.assertEqual(data, {"id": 7})
        self.assertEqual(len(reqs), 2)

    def test_panne_reseau_reessayee_puis_abandonnee(self):
        with self.assertRaises(TmdbError):
            self.call([URLError("coupure")] * 3, attempts=3)

    def test_json_illisible(self):
        with self.assertRaises(TmdbError):
            self.call([FakeResponse(b"<html>maintenance</html>")])


class TestImages(ClientTestCase):
    def test_telechargement_tronque_detecte(self):
        # Sans ce controle, un JPEG coupe finissait embarque tel quel dans le .mkv.
        with self.assertRaises(TmdbError):
            self.call([FakeResponse(b"12345", {"Content-Length": "99"})],
                      methode="image", args=("/a.jpg", "w300"))

    def test_image_complete(self):
        data, _ = self.call([FakeResponse(b"12345", {"Content-Length": "5"})],
                            methode="image", args=("/a.jpg", "w300"))
        self.assertEqual(data, b"12345")


class TestRegion(unittest.TestCase):
    def test_region_extraite_de_la_langue(self):
        self.assertEqual(release_region("fr-FR"), "FR")
        self.assertEqual(release_region("pt-BR"), "BR")
        self.assertIsNone(release_region("fr"))




class TestCacheDesReponses(ClientTestCase):
    def client(self, reponses, cache, **kwargs):
        """Comme ClientTestCase.call, mais avec un cache et plusieurs appels.

        Le remplacement d'urlopen tient jusqu'a la fin du test : sans quoi les
        appels partiraient pour de bon des le retour de cette methode.
        """
        requetes = []

        def fake_urlopen(request, timeout=None):
            requetes.append(request)
            return reponses.pop(0)

        patch = mock.patch.object(tmdb_mod, "urlopen", fake_urlopen)
        patch.start()
        self.addCleanup(patch.stop)
        return Tmdb("0123456789abcdef", cache=cache, **kwargs), requetes

    def test_seconde_requete_servie_par_le_cache(self):
        with tempfile.TemporaryDirectory() as d:
            c = cache.Cache(Path(d))
            client, requetes = self.client([FakeResponse(b'{"id": 1}')], c)
            self.assertEqual(client.get("movie/1"), {"id": 1})
            self.assertEqual(client.get("movie/1"), {"id": 1})   # plus aucun appel reseau
            self.assertEqual(len(requetes), 1)

    def test_la_cle_de_cache_ignore_la_cle_d_api(self):
        # Elle ne doit jamais se retrouver sur le disque.
        with tempfile.TemporaryDirectory() as d:
            c = cache.Cache(Path(d))
            client, _ = self.client([FakeResponse(b"{}")], c)
            client.get("movie/1")
            contenus = [f.read_text(encoding="utf-8") for f in Path(d).glob("*.json")]
            self.assertTrue(contenus)
            self.assertFalse(any("0123456789abcdef" in x for x in contenus))
            self.assertTrue(any('"fr-FR:movie/1"' in x for x in contenus))

    def test_langues_differentes_ne_se_melangent_pas(self):
        with tempfile.TemporaryDirectory() as d:
            c = cache.Cache(Path(d))
            client, requetes = self.client(
                [FakeResponse(b'{"t": "fr"}'), FakeResponse(b'{"t": "en"}')], c)
            self.assertEqual(client.get("tv/1"), {"t": "fr"})
            self.assertEqual(client.get("tv/1", "en-US"), {"t": "en"})
            self.assertEqual(len(requetes), 2)

    def test_reponse_illisible_non_mise_en_cache(self):
        with tempfile.TemporaryDirectory() as d:
            c = cache.Cache(Path(d))
            client, _ = self.client([FakeResponse(b"<html>")], c)
            with self.assertRaises(TmdbError):
                client.get("movie/1")
            self.assertEqual(list(Path(d).glob("*.json")), [])

    def test_sans_cache_le_client_marche_pareil(self):
        client, requetes = self.client([FakeResponse(b'{"id": 1}')], None)
        self.assertEqual(client.get("movie/1"), {"id": 1})
        self.assertEqual(len(requetes), 1)


if __name__ == "__main__":
    unittest.main()
