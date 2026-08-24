"""Appariement d'un dossier de saga a une collection TMDB (aucun reseau)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import saga

RESIDENT_EVIL = [
    {"id": 1576, "title": "Resident Evil", "release_date": "2002-03-15"},
    {"id": 1577, "title": "Resident Evil : Apocalypse", "release_date": "2004-09-10"},
    {"id": 1578, "title": "Resident Evil : Chapitre Final", "release_date": "2016-12-23"},
]


class TestRessemblance(unittest.TestCase):
    def test_le_sous_titre_suffit(self):
        # Dans un dossier de saga, le fichier ne porte que le sous-titre.
        self.assertEqual(saga.close_to("Apocalypse", "Resident Evil : Apocalypse"), 1.0)

    def test_le_titre_complet_marche_aussi(self):
        self.assertEqual(saga.close_to("Resident Evil", "Resident Evil"), 1.0)

    def test_titre_etranger(self):
        self.assertLess(saga.close_to("Ground Zero", "Resident Evil : Apocalypse"), 0.5)


class TestOrdreDeSortie(unittest.TestCase):
    def test_tri_par_date(self):
        melange = [RESIDENT_EVIL[2], RESIDENT_EVIL[0], RESIDENT_EVIL[1]]
        self.assertEqual([p["id"] for p in saga.by_release(melange)], [1576, 1577, 1578])

    def test_sans_date_a_la_fin(self):
        parts = [{"id": 2}, {"id": 1, "release_date": "1999-01-01"}]
        self.assertEqual([p["id"] for p in saga.by_release(parts)], [1, 2])


class TestAppariement(unittest.TestCase):
    def cles(self, resultat):
        return {cle: part["id"] for cle, part in resultat}

    def test_le_numero_place_le_film(self):
        # "22 - Le defi" doit prendre le 22e volume, meme si un autre titre de la
        # saga ressemble davantage.
        parts = [{"id": 101, "title": "Vol. 1 : Episode", "release_date": "1961-01-01"},
                 {"id": 102, "title": "Vol. 2 : Episode", "release_date": "1962-01-01"},
                 {"id": 103, "title": "Vol. 3 : Episode", "release_date": "1963-01-01"}]
        resultat = self.cles(saga.assign([("f", "Episode", 3)], parts))
        self.assertEqual(resultat, {"f": 103})

    def test_l_evidence_puis_l_elimination(self):
        # "Apocalypse" se place tout seul ; "Ground Zero" et "The final Chapter"
        # ne ressemblent a rien et heritent de ce qui reste.
        fichiers = [("a", "Ground Zero", 1), ("b", "Apocalypse", 2),
                    ("c", "The final Chapter", 3)]
        self.assertEqual(self.cles(saga.assign(fichiers, RESIDENT_EVIL)),
                         {"a": 1576, "b": 1577, "c": 1578})

    def test_un_film_de_la_saga_ne_sert_qu_une_fois(self):
        fichiers = [("a", "Apocalypse", None), ("b", "Apocalypse", None)]
        resultat = saga.assign(fichiers, RESIDENT_EVIL)
        self.assertEqual(len(resultat), 1)

    def test_un_titre_muet_sans_numero_n_est_pas_force(self):
        # Sans numero, rien ne dit que "Ground Zero" est le premier film : mieux
        # vaut ne rien dire que de lui coller une fiche au hasard.
        resultat = saga.assign([("a", "Ground Zero", None)], RESIDENT_EVIL)
        self.assertEqual(resultat, [])

    def test_un_film_etranger_a_la_saga_reste_de_cote(self):
        fichiers = [("a", "Apocalypse", None), ("b", "Un tout autre film", None)]
        self.assertEqual(self.cles(saga.assign(fichiers, RESIDENT_EVIL)), {"a": 1577})

    def test_sans_collection_rien_a_apparier(self):
        self.assertEqual(saga.assign([("a", "Apocalypse", 1)], []), [])

    def test_resultat_deterministe(self):
        fichiers = [("a", "Apocalypse", None), ("b", "Apocalypse", None)]
        self.assertEqual(saga.assign(fichiers, RESIDENT_EVIL),
                         saga.assign(fichiers, RESIDENT_EVIL))


class TestSagaDuDossier(unittest.TestCase):
    def test_deux_films_font_une_piste(self):
        self.assertEqual(saga.most_common_collection([7, 7, None, None]), 7)

    def test_un_seul_film_n_en_fait_pas_une(self):
        # Suivre un unique film, ce serait suivre une association peut-etre fausse.
        self.assertIsNone(saga.most_common_collection([9, None, None]))

    def test_aucune_collection(self):
        self.assertIsNone(saga.most_common_collection([None, None]))


class TestControleDeNumerotation(unittest.TestCase):
    """Ce qui separe une saga d un dossier de rangement."""

    def test_le_mcu_ne_tient_pas_dans_une_saga_de_quatre(self):
        # 35 films numerotes, une collection de 4 : sans ce controle, le film
        # n. 4 se ferait placer au 4e rang d une saga qui n est pas la sienne.
        fichiers = [(f"f{i}", "Film", i) for i in range(1, 36)]
        self.assertFalse(saga.fits(fichiers, RESIDENT_EVIL))

    def test_une_vraie_saga_tient(self):
        fichiers = [("a", "Ground Zero", 1), ("b", "Apocalypse", 2)]
        self.assertTrue(saga.fits(fichiers, RESIDENT_EVIL))

    def test_sans_numero_rien_a_verifier(self):
        # Un dossier sans numerotation ne peut pas se contredire : c est la
        # ressemblance des titres qui protege, pas le comptage.
        self.assertTrue(saga.fits([("a", "Apocalypse", None)], RESIDENT_EVIL))

    def test_un_demi_numero_ne_compte_pas(self):
        # "1.5 - Dark Fury" n est pas un rang dans la collection.
        self.assertTrue(saga.fits([("a", "Dark Fury", "1.5")], RESIDENT_EVIL))


if __name__ == "__main__":
    unittest.main()
