"""Renommage des dossiers de films : ce qui est vise, et ce qui est protege."""

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import naming
from Movies import Rename_Movies as renommeur

DUNE = {"id": 438631, "title": "Dune", "release_date": "2021-09-15"}


class FauxTmdb:
    """Rend toujours le meme film ; note les recherches et les acces par id."""

    def __init__(self, film=None):
        self.film = film or DUNE
        self.recherches, self.par_id = [], []

    def search_movie(self, title, year=None):
        self.recherches.append((title, year))
        return [self.film]

    def movie(self, movie_id, language=None):
        self.par_id.append(movie_id)
        return dict(self.film, id=int(movie_id))


class RenameMoviesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.racine = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def creer_dossiers(self, *noms):
        for nom in noms:
            (self.racine / nom).mkdir()
            (self.racine / nom / "film.mkv").write_text("x", encoding="utf-8")

    def creer_fichiers(self, *noms):
        for nom in noms:
            (self.racine / nom).write_text("x", encoding="utf-8")

    def lancer(self, apply=True, pin_id=False, tmdb=None):
        tmdb = tmdb or FauxTmdb()
        args = types.SimpleNamespace(apply=apply, pin_id=pin_id)
        movies = naming.find_movies(self.racine)
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            tally = renommeur.rename_library(movies, args, tmdb)
        self.sortie = sortie.getvalue()
        return tally, sorted(p.name for p in self.racine.iterdir()), tmdb


class TestNomVise(unittest.TestCase):
    def test_titre_et_annee(self):
        self.assertEqual(renommeur.target_stem(DUNE, None, False), "Dune (2021)")

    def test_ordre_de_saga_conserve(self):
        # L'ordre vient de l'utilisateur, pas de TMDB : il doit survivre.
        self.assertEqual(renommeur.target_stem(DUNE, 2, False), "2 - Dune (2021)")

    def test_identifiant_epingle(self):
        self.assertEqual(renommeur.target_stem(DUNE, None, True), "Dune (2021) [tmdbid-438631]")

    def test_sans_annee_connue(self):
        self.assertEqual(renommeur.target_stem({"id": 1, "title": "Inedit"}, None, False), "Inedit")

    def test_caracteres_interdits_retires(self):
        film = {"id": 1, "title": "Mission: Impossible", "release_date": "1996-05-22"}
        self.assertEqual(renommeur.target_stem(film, None, False), "Mission Impossible (1996)")

    def test_epinglage_conserve_sans_l_option(self):
        # Regression : un passage sans --pin-id retirait les identifiants deja poses.
        self.assertTrue(renommeur.wants_pin("Dune (2021) [tmdbid-438631]", False))
        self.assertFalse(renommeur.wants_pin("Dune (2021)", False))
        self.assertTrue(renommeur.wants_pin("Dune (2021)", True))


class TestRenommage(RenameMoviesTestCase):
    def test_dossier_renomme(self):
        self.creer_dossiers("dune.2021.1080p.WEB-DL")
        tally, contenu, _ = self.lancer()
        self.assertEqual(contenu, ["Dune (2021)"])
        self.assertEqual((tally.named, tally.total), (1, 1))

    def test_deja_au_bon_nom(self):
        self.creer_dossiers("Dune (2021)")
        tally, contenu, tmdb = self.lancer()
        self.assertEqual(contenu, ["Dune (2021)"])
        self.assertEqual(tally.named, 1)
        self.assertIn("deja au bon nom", self.sortie)

    def test_simulation_ne_touche_a_rien(self):
        self.creer_dossiers("dune.2021")
        _, contenu, _ = self.lancer(apply=False)
        self.assertEqual(contenu, ["dune.2021"])
        self.assertIn("Dune (2021)", self.sortie)

    def test_ordre_de_saga_preserve(self):
        self.creer_dossiers("1 - dune 2021")
        _, contenu, _ = self.lancer()
        self.assertEqual(contenu, ["1 - Dune (2021)"])

    def test_identifiant_epingle_puis_reutilise(self):
        self.creer_dossiers("dune 2021")
        _, contenu, _ = self.lancer(pin_id=True)
        self.assertEqual(contenu, ["Dune (2021) [tmdbid-438631]"])
        tally, contenu, tmdb = self.lancer()          # second passage
        self.assertEqual(contenu, ["Dune (2021) [tmdbid-438631]"])
        self.assertEqual(tally.named, 1)
        self.assertEqual(tmdb.recherches, [])         # plus rien a chercher
        self.assertEqual(tmdb.par_id, ["438631"])   # l'id epingle est du texte

    def test_le_proprietaire_du_nom_est_celui_qui_le_porte(self):
        # Regression : le dossier deja correct etait declare doublon si un autre
        # visait son nom avant lui.
        self.creer_dossiers("Dune (2021)", "dune bis")
        tally, contenu, _ = self.lancer()
        self.assertIn("Dune (2021)", contenu)
        self.assertIn("dune bis", contenu)            # l'autre n'ecrase rien
        self.assertEqual(tally.named, 1)

    def test_fichiers_a_plat_avec_sous_titres(self):
        self.creer_fichiers("dune.2021.mkv", "dune.2021.fr.srt", "notes.txt")
        tally, contenu, _ = self.lancer()
        self.assertEqual(contenu, ["Dune (2021).fr.srt", "Dune (2021).mkv", "notes.txt"])
        self.assertEqual((tally.named, tally.subtitles), (1, 1))

    def test_film_introuvable_laisse_en_place(self):
        class Vide(FauxTmdb):
            def search_movie(self, title, year=None):
                return []
        self.creer_dossiers("zzz inconnu")
        tally, contenu, _ = self.lancer(tmdb=Vide())
        self.assertEqual(contenu, ["zzz inconnu"])
        self.assertEqual(tally.named, 0)
        self.assertIn("NON ASSOCIE", self.sortie)


    def test_dossier_de_saga_renomme_les_films_pas_le_dossier(self):
        # Le dossier de saga n'est pas un film : il garde son nom, et ce sont les
        # .mkv qu'il contient qui prennent le leur.
        (self.racine / "Saga").mkdir()
        for nom in ("1 - dune 2021.mkv", "2 - dune bis.mkv"):
            (self.racine / "Saga" / nom).write_text("x", encoding="utf-8")
        tally, contenu, _ = self.lancer()
        self.assertEqual(contenu, ["Saga"])
        self.assertEqual(sorted(p.name for p in (self.racine / "Saga").iterdir()),
                         ["1 - Dune (2021).mkv", "2 - Dune (2021).mkv"])
        self.assertEqual((tally.named, tally.total), (2, 2))

    def test_film_seul_dans_son_dossier_renomme_le_dossier(self):
        # Regression : la recursion ne doit pas faire perdre le cas courant.
        (self.racine / "Saga").mkdir()
        (self.racine / "Saga" / "dune 2021").mkdir()
        (self.racine / "Saga" / "dune 2021" / "film.mkv").write_text("x", encoding="utf-8")
        _, contenu, _ = self.lancer()
        self.assertEqual(contenu, ["Saga"])
        self.assertEqual([p.name for p in (self.racine / "Saga").iterdir()],
                         ["Dune (2021)"])

if __name__ == "__main__":
    unittest.main()
