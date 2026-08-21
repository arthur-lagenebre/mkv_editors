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



class TestAssociation(unittest.TestCase):
    EPISODES = [{"episode_number": 1, "name": "Le debut"},
                {"episode_number": 2, "name": "Warhammer 40,000 : et ils marcheront"}]

    def test_numero_prioritaire_sur_le_titre(self):
        ep, methode = naming.match_episode("S01E02 - Le debut.mkv", self.EPISODES, 0.55)
        self.assertEqual(ep["episode_number"], 2)
        self.assertIn("depuis le nom", methode)

    def test_repli_sur_le_titre(self):
        ep, methode = naming.match_episode("Warhammer 40,000.mkv", self.EPISODES, 0.55)
        self.assertEqual(ep["episode_number"], 2)
        self.assertIn("titre", methode)

    def test_sous_le_seuil_rien_n_est_associe(self):
        ep, _ = naming.match_episode("bande annonce.mkv", self.EPISODES, 0.95)
        self.assertIsNone(ep)

    def test_numero_absent_du_jeu_de_donnees(self):
        ep, _ = naming.match_episode("S01E99.mkv", self.EPISODES, 0.95)
        self.assertIsNone(ep)


class TestInventaire(unittest.TestCase):
    EPISODES = [{"episode_number": n, "name": f"Episode {n}"} for n in range(1, 6)]

    def inventaire(self, *noms, seuil=0.55):
        with tempfile.TemporaryDirectory() as d:
            for nom in noms:
                (Path(d) / nom).write_text(nom, encoding="utf-8")
            return naming.owned_numbers(Path(d), self.EPISODES, seuil)

    def test_numeros_reperes(self):
        self.assertEqual(self.inventaire("01 - Episode 1.mkv", "S01E03.mp4"), {1, 3})

    def test_tous_formats_video(self):
        # L'inventaire sert aussi aux series qui ne sont pas en .mkv (--no-tag).
        self.assertEqual(self.inventaire("02 - x.avi", "04 - y.mp4"), {2, 4})

    def test_non_video_ignores(self):
        self.assertEqual(self.inventaire("01 - Episode 1.srt", "notes.txt"), set())

    def test_dossier_absent(self):
        self.assertEqual(naming.owned_numbers(Path("nexiste_pas_du_tout"), self.EPISODES), set())

    def test_dossier_vide(self):
        self.assertEqual(self.inventaire(), set())


class TestSpeciaux(unittest.TestCase):
    def test_dossiers_de_speciaux_valent_la_saison_zero(self):
        # TMDB range les episodes speciaux en saison 0, mais le dossier
        # s'appelle rarement "Saison 0".
        for nom in ("Specials", "Special", "specials", "Hors-serie", "Hors series"):
            with self.subTest(nom=nom):
                self.assertEqual(naming.season_number(nom), 0)

    def test_dossiers_annexes_non_confondus(self):
        # "Bonus" contient des making-of, pas des episodes TMDB.
        for nom in ("Bonus", "Making of", "Extras"):
            with self.subTest(nom=nom):
                self.assertIsNone(naming.season_number(nom))

    def test_specials_trie_avant_la_saison_un(self):
        with tempfile.TemporaryDirectory() as d:
            for nom in ("Saison 1", "Specials"):
                (Path(d) / nom).mkdir()
            self.assertEqual([n for _, n in naming.find_seasons(d)], [0, 1])




class TestIdEpingle(unittest.TestCase):
    def test_forme_jellyfin(self):
        self.assertEqual(naming.extract_tmdb_id("Dune (2021) [tmdbid-438631]"),
                         ("438631", "Dune (2021)"))

    def test_forme_kodi(self):
        self.assertEqual(naming.extract_tmdb_id("Dune {tmdb-438631}"), ("438631", "Dune"))

    def test_casse_indifferente(self):
        self.assertEqual(naming.extract_tmdb_id("Ma Serie [TMDBID-1396]")[0], "1396")

    def test_sans_marqueur(self):
        self.assertEqual(naming.extract_tmdb_id("Dune (2021)"), (None, "Dune (2021)"))

    def test_marqueur_retire_avant_l_analyse_du_titre(self):
        # Sans nettoyage, "tmdbid 438631" se retrouverait dans la recherche.
        _, nom = naming.extract_tmdb_id("Dune (2021) [tmdbid-438631]")
        self.assertEqual(naming.parse_title_year(nom), ("Dune", "2021", None))


if __name__ == "__main__":
    unittest.main()
