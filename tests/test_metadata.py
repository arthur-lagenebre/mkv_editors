"""Etiquetage : plan d'une saison, tags produits, choix du film sur TMDB."""

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import mkv
from Movies import Metadata as films
from TV_Shows import Metadata as series

OPTS = mkv.Options(cover=False, date=True, audio_names=True, sub_names=True,
                   flags=True, stats=True, image_size="w780")
SAISON = {"season_number": 1, "name": "Saison 1", "poster_path": "/p.jpg", "episodes": [
    {"episode_number": 1, "name": "Pilote", "overview": "Debut", "air_date": "2024-01-02",
     "crew": [{"name": "R. Real", "job": "Director", "department": "Directing"}],
     "guest_stars": [{"name": "A. Acteur", "character": "Lui-meme"}],
     "vote_average": 7.84, "vote_count": 12, "still_path": "/s1.jpg"},
    {"episode_number": 2, "name": "Suite", "air_date": "2024-01-09"},
]}


class TestPlanDeSaison(unittest.TestCase):
    def setUp(self):
        self.args = types.SimpleNamespace(probe=False, verify=False, apply=False,
                                          skip_done=False, match_threshold=0.55,
                                          series_name="Ma Serie")

    def process(self, dossier):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            resultat = series.process_season(dossier, SAISON, self.args, OPTS, None)
        return resultat, sortie.getvalue()

    def test_dossier_sans_mkv(self):
        # Regression : le retour a 2 valeurs contre 3 attendues faisait planter main().
        with tempfile.TemporaryDirectory() as d:
            resultat, sortie = self.process(Path(d))
        self.assertEqual(resultat, (0, 0))
        self.assertIn("Aucun .mkv", sortie)

    def test_fichier_illisible_reste_non_bloquant(self):
        # mkvmerge refusera ce faux .mkv : le script doit le dire et continuer.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "01 - x.mkv").write_text("pas un vrai mkv", encoding="utf-8")
            (matched, total), sortie = self.process(Path(d))
        self.assertEqual((matched, total), (1, 1))
        self.assertIn("[S01E01]", sortie)


class TestTagsEpisode(unittest.TestCase):
    def setUp(self):
        xml = series.build_tags_xml(SAISON, SAISON["episodes"][0], "Ma Serie")
        self.root = ElementTree.fromstring(xml)

    def valeur(self, cible, nom):
        """Texte du tag `nom` au niveau `cible` (ElementTree ne sait pas filtrer sur
        un predicat imbrique : on retrouve le bloc a la main)."""
        for tag in self.root.findall("Tag"):
            niveau = tag.find("Targets/TargetTypeValue")
            if niveau is not None and niveau.text == str(cible):
                node = tag.find(f"Simple[Name='{nom}']/String")
                if node is not None:
                    return node.text
        return None

    def test_trois_niveaux_de_cible(self):
        niveaux = [n.text for n in self.root.findall("./Tag/Targets/TargetTypeValue")]
        self.assertEqual(niveaux, ["70", "60", "50"])

    def test_niveau_serie_et_saison(self):
        self.assertEqual(self.valeur(70, "TITLE"), "Ma Serie")
        self.assertEqual(self.valeur(60, "TOTAL_PARTS"), "2")

    def test_niveau_episode(self):
        self.assertEqual(self.valeur(50, "TITLE"), "Pilote")
        self.assertEqual(self.valeur(50, "DATE_RELEASED"), "2024-01-02")
        self.assertEqual(self.valeur(50, "DIRECTOR"), "R. Real")
        self.assertEqual(self.valeur(50, "ACTOR"), "A. Acteur (Lui-meme)")
        self.assertEqual(self.valeur(50, "COMMENT"), "TMDB 7.8/10 (12 votes)")

    def test_cible_de_l_episode(self):
        target = series.episode_target(SAISON, SAISON["episodes"][0], "Ma Serie", OPTS)
        self.assertEqual((target.title, target.date), ("Pilote", "2024-01-02"))
        self.assertIsNone(target.poster)                  # opts.cover est False

    def test_jaquette_repliee_sur_l_affiche_de_saison(self):
        opts = mkv.Options(cover=True, date=True, audio_names=True, sub_names=True,
                           flags=True, stats=True, image_size="w780")
        sans_vignette = series.episode_target(SAISON, SAISON["episodes"][1], "Ma Serie", opts)
        self.assertEqual(sans_vignette.poster, "/p.jpg")


class TestFilms(unittest.TestCase):
    def test_detection_par_dossier(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            (racine / "Inception (2010)").mkdir()
            (racine / "Inception (2010)" / "petit.mkv").write_text("x" * 10, encoding="utf-8")
            (racine / "Inception (2010)" / "film.mkv").write_text("x" * 500, encoding="utf-8")
            movies, foldered = films.find_movies(racine)
        self.assertTrue(foldered)
        self.assertEqual(len(movies), 1)
        self.assertEqual(movies[0][1].name, "film.mkv")      # le plus gros fichier
        self.assertEqual(movies[0][2], "Inception (2010)")   # nom = celui du dossier

    def test_detection_a_plat(self):
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            (racine / "Heat (1995).mkv").write_text("x", encoding="utf-8")
            movies, foldered = films.find_movies(racine)
        self.assertFalse(foldered)
        self.assertEqual([m[2] for m in movies], ["Heat (1995)"])

    def test_remake_signale(self):
        resultats = [{"id": 1, "title": "Dune", "release_date": "2021-09-15"},
                     {"id": 2, "title": "Dune", "release_date": "1984-12-14"}]
        best, notes = films.pick_result(resultats, "Dune")
        self.assertEqual(best["id"], 1)
        self.assertEqual(len(notes), 1)
        self.assertIn("id 2", notes[0])

    def test_titre_eloigne_signale(self):
        _, notes = films.pick_result([{"id": 9, "title": "Autre chose"}], "Inception")
        self.assertTrue(any("eloigne" in n for n in notes))

    def test_resultat_evident_sans_remarque(self):
        resultats = [{"id": 1, "title": "Inception", "release_date": "2010-07-14"},
                     {"id": 2, "title": "Inception: The Cobol Job", "release_date": "2010-12-07"}]
        best, notes = films.pick_result(resultats, "Inception")
        self.assertEqual((best["id"], notes), (1, []))

    def test_tags_de_saga(self):
        movie = {"title": "Iron Man", "release_date": "2008-04-30", "_order": 1,
                 "belongs_to_collection": {"name": "Iron Man - Saga"},
                 "genres": [{"name": "Action"}, {"name": "Science-Fiction"}],
                 "credits": {"crew": [{"name": "J. Favreau", "job": "Director"}],
                             "cast": [{"name": "R. Downey Jr.", "character": "Tony Stark"}]}}
        root = ElementTree.fromstring(films.build_movie_tags_xml(movie))
        niveaux = [n.text for n in root.findall("./Tag/Targets/TargetTypeValue")]
        self.assertEqual(niveaux, ["70", "50"])
        saga = root.find("Tag")
        self.assertEqual(saga.find("Simple[Name='TITLE']/String").text, "Iron Man - Saga")
        self.assertEqual(saga.find("Simple[Name='PART_NUMBER']/String").text, "1")
        film = root.findall("Tag")[1]
        self.assertEqual(film.find("Simple[Name='GENRE']/String").text, "Action, Science-Fiction")

    def test_film_hors_saga(self):
        movie = {"title": "Heat", "credits": {}}
        root = ElementTree.fromstring(films.build_movie_tags_xml(movie))
        self.assertEqual([n.text for n in root.findall("./Tag/Targets/TargetTypeValue")], ["50"])


if __name__ == "__main__":
    unittest.main()
