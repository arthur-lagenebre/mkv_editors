"""Analyse des noms de fichiers : la partie la plus facile a casser en silence."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import naming


class TestTitreAnnee(unittest.TestCase):
    def test_annee_entre_parentheses(self):
        self.assertEqual(naming.parse_title_year("Inception (2010)"), ("Inception", "2010", None))

    def test_prefixe_d_ordre_de_saga(self):
        self.assertEqual(naming.parse_title_year("1 - Iron Man (2008)"), ("Iron Man", "2008", 1))
        self.assertEqual(naming.parse_title_year("13 - Le Loup de Wall Street (2013)"),
                         ("Le Loup de Wall Street", "2013", 13))

    def test_tiret_dans_le_titre_n_est_pas_un_ordre(self):
        title, year, order = naming.parse_title_year("Avatar 2 - La Voie de l'eau (2022)")
        self.assertEqual((title, year, order), ("Avatar 2 - La Voie de l'eau", "2022", None))

    def test_nombre_du_titre_pris_pour_une_annee(self):
        # Regression : '2049' etait retenu comme annee, laissant chercher 'Blade Runner'.
        self.assertEqual(naming.parse_title_year("Blade Runner 2049"),
                         ("Blade Runner 2049", None, None))

    def test_titre_qui_est_un_nombre(self):
        # Regression : le titre devenait vide, et TMDB etait interroge sans requete.
        self.assertEqual(naming.parse_title_year("2012"), ("2012", "2012", None))

    def test_tokens_de_release_retires(self):
        self.assertEqual(naming.parse_title_year("Blade.Runner.2049.2017.1080p.BluRay.x264"),
                         ("Blade Runner 2049", "2017", None))

    def test_sans_annee(self):
        self.assertEqual(naming.parse_title_year("Mission Impossible 2 [1080p]"),
                         ("Mission Impossible 2", None, None))


class TestNumeroEpisode(unittest.TestCase):
    def test_formats_reconnus(self):
        cas = {
            "Serie.S01E05.mkv": 5,
            "Serie S1E5.mkv": 5,
            "Serie 1x05.mkv": 5,
            "Serie - Episode 7.mkv": 7,
            "Ep03 - Titre.mkv": 3,
            "04 - Titre.mkv": 4,
            "12. Titre.mkv": 12,
        }
        for nom, attendu in cas.items():
            with self.subTest(nom=nom):
                self.assertEqual(naming.detect_episode_number(nom), attendu)

    def test_resolution_n_est_pas_un_numero(self):
        self.assertIsNone(naming.detect_episode_number("Film.1920x1080.mkv"))

    def test_aucun_numero(self):
        self.assertIsNone(naming.detect_episode_number("Titre sans numero.mkv"))


class TestSaisons(unittest.TestCase):
    def test_numero_de_saison(self):
        for nom, attendu in {"Saison 1": 1, "Season 02": 2, "S3": 3, "Saison_10": 10,
                             "Bonus": None, "Saison X": None}.items():
            with self.subTest(nom=nom):
                self.assertEqual(naming.season_number(nom), attendu)

    def test_find_seasons_trie_par_numero(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for nom in ("Saison 10", "Saison 2", "Bonus"):
                (root / nom).mkdir()
            self.assertEqual([n for _, n in naming.find_seasons(root)], [2, 10])

    def test_find_seasons_vide_si_aucune(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(naming.find_seasons(d), [])


class TestNomsEcrits(unittest.TestCase):
    def test_caracteres_interdits_windows(self):
        brut = 'A<B>C:D"E/F' + chr(92) + 'G|H?I*J'
        self.assertEqual(naming.safe_name(brut), "ABCDEFGHIJ")

    def test_point_final_supprime(self):
        self.assertEqual(naming.safe_name("Fin de partie..."), "Fin de partie")

    def test_date_francaise(self):
        self.assertEqual(naming.fr_date("2024-12-10"), "10 décembre 2024")
        self.assertEqual(naming.fr_date(""), "")
        self.assertEqual(naming.fr_date("pas une date"), "pas une date")


if __name__ == "__main__":
    unittest.main()
