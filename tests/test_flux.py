"""Déroulement complet des scripts, TMDB simule : ce que produit une invocation.

Les tests unitaires couvrent les pièces ; ceux-ci vérifient qu'elles sont bien reliées entre elles - c'est la que se logent les options qui ne font rien.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.Movies import Metadata as films
from scripts.TV_Shows import Rename_Episodes as rename

class FauxTmdb:
    """Répond comme TMDB, sans réseau, et note ce qu'on lui à demande."""

    def __init__(self):
        self.saisons = []

    def search_movie(self, title, year=None):
        return [{"id": 1, "title": "Dune", "release_date": "2021-09-15"}]

    def movie(self, movie_id, language=None):
        return {"id": 1, "title": "Dune", "release_date": "2021-09-15", "poster_path": "/dune.jpg", "credits": {}}

    def local_release_date(self, movie_id, region):
        return None

    def image(self, image_path, size):
        return b"jpeg"

    def save_image(self, image_path, size, dest):
        Path(dest).write_bytes(self.image(image_path, size))

    def season(self, show_id, season_number, language=None):
        self.saisons.append(season_number)
        return {"season_number": season_number, "episodes": [{"episode_number": 1, "name": "Special"}]}


class FluxTestCase(unittest.TestCase):
    def lancer(self, module, argv, tmdb=None):
        """Exécute main() du script avec un TMDB simule. Retourne (tmdb, sortie)."""
        tmdb = tmdb or FauxTmdb()
        sortie = io.StringIO()
        with mock.patch.object(sys, "argv", ["script"] + argv), mock.patch.object(module, "Tmdb", lambda *a, **k: tmdb), mock.patch.object(module.cli, "resolve_tmdb_key", lambda *a: "cle"), redirect_stdout(sortie):
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
        """Lance le script films, sans MKVToolNix : l'écriture dans le .mkv est simulée et notée, ce qu'on regarde ici est ce que le script decide d'écrire."""
        self.ecritures = []

        def faux_write(path, info, target, opts, tmdb):
            self.ecritures.append(Path(path).name)
            return 0, ""

        with mock.patch.object(films.mkv, "check_tools", lambda **k: False), mock.patch.object(films.mkv, "write", faux_write):
            return self.lancer(films, ["--dir", str(self.racine), *options])

    def test_affiche_ecrite_meme_sans_etiquetage(self):
        # Régression : --artwork vivait dans le traitement du fichier, que --no-tag saute - l'option ne produisait donc rien.
        self.lancer_films("--no-tag", "--artwork", "--apply")
        self.assertTrue((self.film / "folder.jpg").exists())

    def test_affiche_ecrite_par_un_etiquetage_normal(self):
        self.lancer_films("--artwork", "--apply")
        self.assertTrue((self.film / "folder.jpg").exists())
        self.assertEqual(self.ecritures, ["film.mkv"])     # le .mkv aussi a été écrit

    def test_verify_n_ecrit_aucune_annexe(self):
        _, sortie = self.lancer_films("--artwork", "--verify")
        self.assertFalse((self.film / "folder.jpg").exists())
        self.assertIn("ecrirait folder.jpg", sortie)

    def test_recap_ecrit_sous_no_tag(self):
        self.lancer_films("--no-tag", "--recap", "--apply")
        self.assertTrue((self.racine / "recap.html").exists())


class TestDoublons(FluxTestCase):
    def test_deux_dossiers_pour_un_film(self):
        # Le récap n'en montrera qu'une vignette (les films y sont indexés par id) : l'écart avec le total doit être expliqué, pas laisse deviner.
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            for nom in ("Dune (2021)", "Dune 4K (2021)"):
                (racine / nom).mkdir()
                (racine / nom / "film.mkv").write_text("x", encoding="utf-8")
            with mock.patch.object(films.mkv, "check_tools", lambda **k: False):
                _, sortie = self.lancer(films, ["--dir", str(racine), "--no-tag"])
        self.assertIn("[DOUBLON]", sortie)
        self.assertIn("TOTAL : 2/2", sortie)       # les deux restent traités


class TestSaisonUnique(FluxTestCase):
    def lancer_rename(self, nom_du_dossier):
        with tempfile.TemporaryDirectory() as d:
            dossier = Path(d) / nom_du_dossier
            dossier.mkdir()
            (dossier / "01 - brut.mkv").write_text("x", encoding="utf-8")
            tmdb, _ = self.lancer(rename, ["--dir", str(dossier), "--tmdb-id", "42"])
            return tmdb.saisons

    def test_dossier_de_speciaux_demande_la_saison_zero(self):
        # Régression : un 'or 1' transformait la saison 0 en saison 1, et les spéciaux se retrouvaient renommés avec les titres des vrais épisodes.
        self.assertEqual(self.lancer_rename("Specials"), [0])

    def test_dossier_de_saison_ordinaire(self):
        self.assertEqual(self.lancer_rename("Saison 3"), [3])

    def test_dossier_sans_numero_vaut_la_premiere_saison(self):
        self.assertEqual(self.lancer_rename("Ma Serie"), [1])

class TestDossierInvalide(FluxTestCase):
    """Une faute de frappe dans --dir doit s'arrêter net, avant tout appel TMDB."""

    def echec(self, module, dossier, options=()):
        tmdb = FauxTmdb()
        with self.assertRaises(SystemExit) as ctx:
            self.lancer(module, ["--dir", str(dossier), *options], tmdb)
        return str(ctx.exception), tmdb

    def test_films_dossier_introuvable(self):
        message, _ = self.echec(films, "dossier_qui_n_existe_pas")
        self.assertIn("introuvable", message)

    def test_rename_dossier_introuvable(self):
        # Régression : levait une FileNotFoundError brute en pleine figure.
        message, tmdb = self.echec(rename, "dossier_qui_n_existe_pas", ["--tmdb-id", "42"])
        self.assertIn("introuvable", message)
        self.assertEqual(tmdb.saisons, [])          # arrêt avant le réseau

    def test_dir_sur_un_fichier(self):
        with tempfile.TemporaryDirectory() as d:
            fichier = Path(d) / "film.mkv"
            fichier.write_text("x", encoding="utf-8")
            message, _ = self.echec(rename, fichier, ["--tmdb-id", "42"])
        self.assertIn("pas un fichier", message)

if __name__ == "__main__":
    unittest.main()
