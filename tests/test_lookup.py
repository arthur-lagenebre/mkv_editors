"""Association d'un nom de dossier a une fiche TMDB (films et series)."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import lookup
from mkvlib.tmdb import TmdbError


class FauxTmdb:
    """Client TMDB minimal : rejoue des reponses, retient les recherches faites."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.appels = []

    def search_tv(self, name, year=None):
        self.appels.append((name, year))
        reponse = self.reponses.pop(0)
        if isinstance(reponse, Exception):
            raise reponse
        return reponse


class TestChoixDuResultat(unittest.TestCase):
    def test_remake_signale(self):
        resultats = [{"id": 1, "title": "Dune", "release_date": "2021-09-15"},
                     {"id": 2, "title": "Dune", "release_date": "1984-12-14"}]
        best, notes = lookup.pick_result(resultats, "Dune")
        self.assertEqual(best["id"], 1)
        self.assertEqual(len(notes), 1)
        self.assertIn("id 2", notes[0])

    def test_titre_eloigne_signale(self):
        _, notes = lookup.pick_result([{"id": 9, "title": "Autre chose"}], "Inception")
        self.assertTrue(any("eloigne" in n for n in notes))

    def test_resultat_evident_sans_remarque(self):
        resultats = [{"id": 1, "title": "Inception", "release_date": "2010-07-14"},
                     {"id": 2, "title": "Inception: The Cobol Job", "release_date": "2010-12-07"}]
        best, notes = lookup.pick_result(resultats, "Inception")
        self.assertEqual((best["id"], notes), (1, []))

    def test_champs_des_series(self):
        resultats = [{"id": 5, "name": "Fargo", "first_air_date": "2014-04-15"},
                     {"id": 6, "name": "Fargo", "first_air_date": "1997-01-01"}]
        best, notes = lookup.pick_result(resultats, "Fargo", key="name", date_key="first_air_date")
        self.assertEqual(best["id"], 5)
        self.assertIn("1997", notes[0])

    def test_description(self):
        self.assertEqual(lookup.describe({"id": 27205, "title": "Inception",
                                          "release_date": "2010-07-14"}),
                         "Inception (2010) [id 27205]")

    def test_description_sans_date(self):
        self.assertEqual(lookup.describe({"id": 1, "title": "X"}), "X (?) [id 1]")


class TestNomDeSerie(unittest.TestCase):
    def query(self, *segments):
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d).joinpath(*segments)
            chemin.mkdir(parents=True)
            return lookup.series_query(chemin)

    def test_dossier_de_serie(self):
        self.assertEqual(self.query("Breaking Bad"), ("Breaking Bad", None))

    def test_annee_extraite(self):
        self.assertEqual(self.query("Fargo (2014)"), ("Fargo", "2014"))

    def test_dossier_de_saison_remonte_au_parent(self):
        # --dir peut pointer sur une saison : c'est le parent qui nomme la serie.
        self.assertEqual(self.query("Breaking Bad", "Saison 2"), ("Breaking Bad", None))

    def test_tokens_de_release_retires(self):
        self.assertEqual(self.query("Dark.S01.1080p.WEB-DL")[0], "Dark S01")


class TestResolutionDeLaSerie(unittest.TestCase):
    def resoudre(self, tmdb, dossier="Ma Serie", forced=None):
        sortie = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / dossier
            chemin.mkdir()
            with redirect_stdout(sortie):
                resultat = lookup.resolve_show_id(tmdb, chemin, forced)
        return resultat, sortie.getvalue()

    def test_id_force_court_circuite_la_recherche(self):
        tmdb = FauxTmdb()
        self.assertEqual(self.resoudre(tmdb, forced="1234")[0], "1234")
        self.assertEqual(tmdb.appels, [])

    def test_recherche_sur_le_nom_du_dossier(self):
        tmdb = FauxTmdb([{"id": 42, "name": "Ma Serie", "first_air_date": "2020-01-01"}])
        identifiant, sortie = self.resoudre(tmdb)
        self.assertEqual(identifiant, 42)
        self.assertEqual(tmdb.appels, [("Ma Serie", None)])
        self.assertIn("id 42", sortie)

    def test_seconde_tentative_sans_l_annee(self):
        tmdb = FauxTmdb([], [{"id": 7, "name": "Fargo", "first_air_date": "2014-04-15"}])
        identifiant, _ = self.resoudre(tmdb, dossier="Fargo (2014)")
        self.assertEqual(identifiant, 7)
        self.assertEqual(tmdb.appels, [("Fargo", "2014"), ("Fargo", None)])

    def test_aucun_resultat(self):
        identifiant, sortie = self.resoudre(FauxTmdb([]))
        self.assertIsNone(identifiant)
        self.assertIn("Aucune serie TMDB", sortie)

    def test_echec_reseau(self):
        identifiant, sortie = self.resoudre(FauxTmdb(TmdbError("coupure")))
        self.assertIsNone(identifiant)
        self.assertIn("Echec de la recherche", sortie)




class TestIdEpingle(unittest.TestCase):
    def resoudre(self, dossier, saison=None, forced=None):
        tmdb = FauxTmdb([{"id": 1, "name": "Cherche"}])
        sortie = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / dossier
            (chemin / saison if saison else chemin).mkdir(parents=True)
            with redirect_stdout(sortie):
                resultat = lookup.resolve_show_id(
                    tmdb, chemin / saison if saison else chemin, forced)
        return resultat, tmdb.appels, sortie.getvalue()

    def test_id_du_nom_evite_la_recherche(self):
        ident, appels, sortie = self.resoudre("Ma Serie [tmdbid-1396]")
        self.assertEqual(ident, "1396")
        self.assertEqual(appels, [])
        self.assertIn("epingle", sortie)

    def test_id_repris_depuis_le_dossier_parent(self):
        ident, appels, _ = self.resoudre("Ma Serie [tmdbid-1396]", saison="Saison 2")
        self.assertEqual((ident, appels), ("1396", []))

    def test_option_prioritaire_sur_le_nom(self):
        ident, _, _ = self.resoudre("Ma Serie [tmdbid-1396]", forced="999")
        self.assertEqual(ident, "999")

    def test_marqueur_retire_de_la_recherche(self):
        self.assertEqual(lookup.series_query(Path("Ma Serie [tmdbid-1396]"))[0], "Ma Serie")


if __name__ == "__main__":
    unittest.main()
