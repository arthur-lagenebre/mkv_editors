"""Deroulement complet des scripts, TMDB simule : ce que produit une invocation.

Les tests unitaires couvrent les pieces ; ceux-ci verifient qu'elles sont bien
reliees entre elles - c'est la que se logent les options qui ne font rien.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Movies import Metadata as films
from TV_Shows import Rename_Episodes as rename


class FauxTmdb:
    """Repond comme TMDB, sans reseau, et note ce qu'on lui a demande."""

    def __init__(self):
        self.saisons = []

    def search_movie(self, title, year=None):
        return [{"id": 1, "title": "Dune", "release_date": "2021-09-15"}]

    def movie(self, movie_id, language=None):
        return {"id": 1, "title": "Dune", "release_date": "2021-09-15",
                "poster_path": "/dune.jpg", "credits": {}}

    def local_release_date(self, movie_id, region):
        return None

    def image(self, image_path, size):
        return b"jpeg"

    def save_image(self, image_path, size, dest):
        Path(dest).write_bytes(self.image(image_path, size))

    def season(self, show_id, season_number, language=None):
        self.saisons.append(season_number)
        return {"season_number": season_number, "episodes": [
            {"episode_number": 1, "name": "Special"}]}


class FluxTestCase(unittest.TestCase):
    def lancer(self, module, argv, tmdb=None):
        """Execute main() du script avec un TMDB simule. Retourne (tmdb, sortie)."""
        tmdb = tmdb or FauxTmdb()
        sortie = io.StringIO()
        with mock.patch.object(sys, "argv", ["script"] + argv), \
             mock.patch.object(module, "Tmdb", lambda *a, **k: tmdb), \
             mock.patch.object(module.cli, "resolve_tmdb_key", lambda *a: "cle"), \
             redirect_stdout(sortie):
            module.main()
        return tmdb, sortie.getvalue()


class TestAnnexesDesFilms(FluxTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.racine = Path(self._tmp.name)
        self.film = self.racine / "Dune (2021)"
        self.film.mkdir()
        (self.film / "film.mkv").write_text("x", encoding="utf-8")
        self.addCleanup(self._tmp.cleanup)

    def lancer_films(self, *options):
        with mock.patch.object(films.mkv, "check_tools", lambda **k: False):
            return self.lancer(films, ["--dir", str(self.racine), *options])

    def test_affiche_ecrite_meme_sans_etiquetage(self):
        # Regression : --artwork vivait dans le traitement du fichier, que
        # --no-tag saute - l'option ne produisait donc rien.
        self.lancer_films("--no-tag", "--artwork", "--apply")
        self.assertTrue((self.film / "folder.jpg").exists())

    def test_affiche_ecrite_par_un_etiquetage_normal(self):
        self.lancer_films("--artwork", "--apply")
        self.assertTrue((self.film / "folder.jpg").exists())

    def test_verify_n_ecrit_aucune_annexe(self):
        _, sortie = self.lancer_films("--artwork", "--verify")
        self.assertFalse((self.film / "folder.jpg").exists())
        self.assertIn("ecrirait folder.jpg", sortie)

    def test_recap_ecrit_sous_no_tag(self):
        self.lancer_films("--no-tag", "--recap", "--apply")
        self.assertTrue((self.racine / "recap.html").exists())


class TestSaisonUnique(FluxTestCase):
    def lancer_rename(self, nom_du_dossier):
        with tempfile.TemporaryDirectory() as d:
            dossier = Path(d) / nom_du_dossier
            dossier.mkdir()
            (dossier / "01 - brut.mkv").write_text("x", encoding="utf-8")
            tmdb, _ = self.lancer(rename, ["--dir", str(dossier), "--tmdb-id", "42"])
            return tmdb.saisons

    def test_dossier_de_speciaux_demande_la_saison_zero(self):
        # Regression : un 'or 1' transformait la saison 0 en saison 1, et les
        # speciaux se retrouvaient renommes avec les titres des vrais episodes.
        self.assertEqual(self.lancer_rename("Specials"), [0])

    def test_dossier_de_saison_ordinaire(self):
        self.assertEqual(self.lancer_rename("Saison 3"), [3])

    def test_dossier_sans_numero_vaut_la_premiere_saison(self):
        self.assertEqual(self.lancer_rename("Ma Serie"), [1])


if __name__ == "__main__":
    unittest.main()
