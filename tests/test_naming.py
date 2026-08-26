"""Analyse des noms de fichiers : la partie la plus facile à casser en silence."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import naming


class TestTitreAnnee(unittest.TestCase):
    def test_demi_numero_d_ordre(self):
        # Les films intercalaires : "1.5 - Dark Fury" était cherché "1 5 - Dark Fury".
        self.assertEqual(naming.parse_title_year("1.5 - Dark Fury"), ("Dark Fury", None, "1.5"))
    def test_annee_entre_parentheses(self):
        self.assertEqual(naming.parse_title_year("Inception (2010)"), ("Inception", "2010", None))

    def test_prefixe_d_ordre_de_saga(self):
        self.assertEqual(naming.parse_title_year("1 - Iron Man (2008)"), ("Iron Man", "2008", 1))
        self.assertEqual(naming.parse_title_year("13 - Le Loup de Wall Street (2013)"), ("Le Loup de Wall Street", "2013", 13))

    def test_tiret_dans_le_titre_n_est_pas_un_ordre(self):
        title, year, order = naming.parse_title_year("Avatar 2 - La Voie de l'eau (2022)")
        self.assertEqual((title, year, order), ("Avatar 2 - La Voie de l'eau", "2022", None))

    def test_nombre_du_titre_pris_pour_une_annee(self):
        # Régression : '2049' était retenu comme année, laissant chercher 'Blade Runner'.
        self.assertEqual(naming.parse_title_year("Blade Runner 2049"), ("Blade Runner 2049", None, None))

    def test_titre_qui_est_un_nombre(self):
        # Régression : le titre devenait vide, et TMDB était interroge sans requête. Le nombre termine le nom : il est le titre, et n'est pas aussi une année (le film "2012" est sorti en 2009 - filtrer la-dessus ne trouvait rien).
        self.assertEqual(naming.parse_title_year("2012"), ("2012", None, None))

    def test_annee_nue_en_fin_de_nom_appartient_au_titre(self):
        # Régression : quatre films y perdaient leur titre, dont "Wonder Woman 1984" qui se retrouvait associe à "Wonder Woman" (2017).
        for nom in ("Wonder Woman 1984", "New York 1997", "Death race 2000"):
            self.assertEqual(naming.parse_title_year(nom), (nom, None, None))

    def test_annee_nue_suivie_de_quelque_chose_reste_une_annee(self):
        # Ce qui la distingue : une année de release est suivie des tokens.
        self.assertEqual(naming.parse_title_year("dune.2021.1080p.WEB-DL"), ("dune", "2021", None))

    def test_prefixe_d_ordre_a_quatre_chiffres(self):
        # Une saga ordonnée par année ("1990 - Les Tortues Ninja") : sans ça, le titre disparaissait et quatre films tombaient sur des inconnus.
        self.assertEqual(naming.parse_title_year("1990 - Les Tortues Ninja"), ("Les Tortues Ninja", None, 1990))

    def test_tokens_de_release_retires(self):
        self.assertEqual(naming.parse_title_year("Blade.Runner.2049.2017.1080p.BluRay.x264"), ("Blade Runner 2049", "2017", None))

    def test_sans_annee(self):
        self.assertEqual(naming.parse_title_year("Mission Impossible 2 [1080p]"), ("Mission Impossible 2", None, None))


class TestNumeroEpisode(unittest.TestCase):
    def test_formats_reconnus(self):
        cas = { "Serie.S01E05.mkv": 5, "Serie S1E5.mkv": 5, "Serie 1x05.mkv": 5, "Serie - Episode 7.mkv": 7, "Ep03 - Titre.mkv": 3, "04 - Titre.mkv": 4, "12. Titre.mkv": 12 }
        for nom, attendu in cas.items():
            with self.subTest(nom=nom):
                self.assertEqual(naming.detect_episode_number(nom), attendu)

    def test_resolution_n_est_pas_un_numero(self):
        self.assertIsNone(naming.detect_episode_number("Film.1920x1080.mkv"))

    def test_aucun_numero(self):
        self.assertIsNone(naming.detect_episode_number("Titre sans numero.mkv"))


class TestSaisons(unittest.TestCase):
    def test_numero_de_saison(self):
        for nom, attendu in {"Saison 1": 1, "Season 02": 2, "S3": 3, "Saison_10": 10, "Bonus": None, "Saison X": None}.items():
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
    EPISODES = [{"episode_number": 1, "name": "Le debut"}, {"episode_number": 2, "name": "Warhammer 40,000 : et ils marcheront"}]

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
        # L'inventaire sert aussi aux séries qui ne sont pas en .mkv (--no-tag).
        self.assertEqual(self.inventaire("02 - x.avi", "04 - y.mp4"), {2, 4})

    def test_non_video_ignores(self):
        self.assertEqual(self.inventaire("01 - Episode 1.srt", "notes.txt"), set())

    def test_dossier_absent(self):
        self.assertEqual(naming.owned_numbers(Path("nexiste_pas_du_tout"), self.EPISODES), set())

    def test_dossier_vide(self):
        self.assertEqual(self.inventaire(), set())


class TestSpeciaux(unittest.TestCase):
    def test_dossiers_de_speciaux_valent_la_saison_zero(self):
        # TMDB range les épisodes spéciaux en saison 0, mais le dossier s'appelle rarement "Saison 0".
        for nom in ("Specials", "Special", "specials", "Hors-serie", "Hors series"):
            with self.subTest(nom=nom):
                self.assertEqual(naming.season_number(nom), 0)

    def test_dossiers_annexes_non_confondus(self):
        # "Bonus" contient des making-of, pas des épisodes TMDB.
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
        self.assertEqual(naming.extract_tmdb_id("Dune (2021) [tmdbid-438631]"), ("438631", "Dune (2021)"))

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

class TestDetectionDesFilms(unittest.TestCase):
    def detecter(self, arborescence):
        """Crée les fichiers décrits ({chemin relatif: taille}) et lance find_movies."""
        with tempfile.TemporaryDirectory() as d:
            racine = Path(d)
            for chemin, taille in arborescence.items():
                fichier = racine / chemin
                fichier.parent.mkdir(parents=True, exist_ok=True)
                fichier.write_text("x" * taille, encoding="utf-8")
            return naming.find_movies(racine)

    def noms(self, movies):
        return [m.rawname for m in movies]

    def test_detection_par_dossier(self):
        movies = self.detecter({"Inception (2010)/bande-annonce.mkv": 10, "Inception (2010)/film.mkv": 500})
        self.assertEqual(len(movies), 1)
        self.assertEqual([f.name for f in movies[0].files], ["film.mkv"])   # le petit est écarte
        self.assertEqual(movies[0].rawname, "Inception (2010)")             # nom = le dossier
        self.assertTrue(movies[0].owns_folder)

    def test_film_en_deux_fichiers(self):
        # Régression : seul le plus gros était étiqueté, l'autre restait nu.
        movies = self.detecter({"Heat (1995)/CD1.mkv": 500, "Heat (1995)/CD2.mkv": 460, "Heat (1995)/making-of.mkv": 40})
        self.assertEqual(len(movies), 1)
        self.assertEqual([f.name for f in movies[0].files], ["CD1.mkv", "CD2.mkv"])

    def test_detection_a_plat(self):
        movies = self.detecter({"Heat (1995).mkv": 1})
        self.assertEqual(self.noms(movies), ["Heat (1995)"])
        self.assertEqual(len(movies[0].files), 1)
        self.assertFalse(movies[0].owns_folder)      # --dir nomme la médiathèque, pas le film

    def test_dossier_sans_video(self):
        self.assertEqual(self.detecter({"Notes/lisezmoi.txt": 5}), [])

    def test_les_films_a_plat_survivent_aux_dossiers(self):
        # Régression : un seul sous-dossier suffisait à faire disparaître, sans un  mot, tous les .mkv posés à la racine.
        movies = self.detecter({"Catwoman.mkv": 100, "Superman (2025).mkv": 100, "Joker/1 - Joker.mkv": 100, "Joker/2 - Folie a deux.mkv": 100})
        self.assertEqual(self.noms(movies), ["Catwoman", "Superman (2025)", "1 - Joker", "2 - Folie a deux"])

    def test_dossier_de_saga_un_film_par_fichier(self):
        # Régression : les deux fichiers passaient pour un seul film en deux parts, et 'Folie à deux' recevait les métadonnées de 'Joker'.
        movies = self.detecter({"Joker/1 - Joker.mkv": 500, "Joker/2 - Folie a deux.mkv": 480})
        self.assertEqual(self.noms(movies), ["1 - Joker", "2 - Folie a deux"])
        self.assertEqual([m.contexts for m in movies], [["Joker"], ["Joker"]])
        self.assertFalse(any(m.owns_folder for m in movies))

    def test_petit_film_a_cote_d_un_remux(self):
        # Régression : 1 Go à côté de 28 Go, c'est un dessin anime à côté d'un remux - pas une bande-annonce. Le poids ne decide plus de rien.
        movies = self.detecter({"Catwoman.mkv": 11, "Constantine.mkv": 280})
        self.assertEqual(self.noms(movies), ["Catwoman", "Constantine"])

    def test_saga_aux_films_de_tailles_tres_inegales(self):
        # Un film deux fois plus léger que le plus gros de la saga reste un film.
        movies = self.detecter({"DCEU/01 - Man of Steel.mkv": 200, "DCEU/05 - Justice League.mkv": 490, "DCEU/09 - Wonder Woman 1984.mkv": 280})
        self.assertEqual(len(movies), 3)

    def test_descente_sans_limite_de_profondeur(self):
        movies = self.detecter({"Batman/Nolan Trilogy/1 - Batman Begins (2005)/film.mkv": 100})
        self.assertEqual(self.noms(movies), ["1 - Batman Begins (2005)"])
        self.assertTrue(movies[0].owns_folder)

    def test_un_dossier_qui_range_ne_prete_pas_son_nom(self):
        # 'Batman' contient un film ET des sous-dossiers : c'est du rangement, pas un film. Le fichier répond de lui-même, le dossier reste en renfort.
        movies = self.detecter({"Batman/The Batman.mkv": 100, "Batman/Nolan Trilogy/1 - Batman Begins.mkv": 100, "Batman/Nolan Trilogy/2 - The Dark Knight.mkv": 100})
        self.assertEqual(self.noms(movies), ["The Batman", "1 - Batman Begins", "2 - The Dark Knight"])
        # Le grand-parent suit le parent : "Nolan Trilogy" ne dit rien à TMDB, "Batman" si.
        self.assertEqual([m.contexts for m in movies], [["Batman"], ["Nolan Trilogy", "Batman"], ["Nolan Trilogy", "Batman"]])
        self.assertFalse(any(m.owns_folder for m in movies))

    def test_dossiers_parents_du_plus_proche_au_plus_lointain(self):
        movies = self.detecter({"Resident Evil/Animation/3 - Vendetta.mkv": 100, "Resident Evil/Animation/4 - Death Island.mkv": 100, "Resident Evil/2 - Apocalypse.mkv": 100, "Seul.mkv": 100})
        contextes = {m.rawname: m.contexts for m in movies}
        self.assertEqual(contextes["Seul"], [])                  # --dir ne compte pas
        self.assertEqual(contextes["2 - Apocalypse"], ["Resident Evil"])
        self.assertEqual(contextes["3 - Vendetta"], ["Animation", "Resident Evil"])

    def test_dossiers_de_bonus_ecartes(self):
        movies = self.detecter({"Dune (2021)/film.mkv": 500, "Dune (2021)/Extras/featurette.mkv": 400})
        self.assertEqual(self.noms(movies), ["Dune (2021)"])

    def test_bonus_seul_dans_son_dossier_reste_un_film(self):
        # Un titre à le droit de contenir 'Bonus' : écarter le seul .mkv du dossier le ferait disparaître en silence.
        movies = self.detecter({"Bonus (2019)/Bonus.mkv": 100})
        self.assertEqual(self.noms(movies), ["Bonus (2019)"])

    def test_marqueur_de_part_retire_du_nom_cherche(self):
        movies = self.detecter({"Saga/Heat CD1.mkv": 500, "Saga/Heat CD2.mkv": 480, "Saga/Collateral.mkv": 490})
        self.assertEqual(self.noms(movies), ["Collateral", "Heat"])

    def test_chemin_affiche(self):
        movies = self.detecter({"DCEU/01 - Man of Steel.mkv": 100, "DCEU/06 - Aquaman.mkv": 100})
        self.assertEqual(movies[0].display, str(Path("DCEU/01 - Man of Steel")))


if __name__ == "__main__":
    unittest.main()
