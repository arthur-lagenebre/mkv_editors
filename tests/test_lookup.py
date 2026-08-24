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

    def test_titre_exact_prefere_a_la_popularite(self):
        # TMDB classe par popularite : "Blade" rend "Blade II" en premier.
        resultats = [{"id": 36586, "title": "Blade II", "vote_count": 5349},
                     {"id": 36647, "title": "Blade", "vote_count": 6892}]
        best, _ = lookup.pick_result(resultats, "Blade")
        self.assertEqual(best["id"], 36647)

    def test_titre_exact_mais_inconnu_ne_prend_pas_la_place(self):
        # Regression : "The Fast and the Furious" (1954, 41 votes) chassait celui
        # de 2001 (11 029 votes). Un titre exact porte par un inconnu ne vaut rien.
        resultats = [{"id": 9799, "title": "Fast and Furious", "vote_count": 11029},
                     {"id": 20174, "title": "The Fast and the Furious", "vote_count": 41}]
        best, _ = lookup.pick_result(resultats, "The Fast and the Furious")
        self.assertEqual(best["id"], 9799)
    def test_un_classique_moins_vote_que_sa_suite_passe_quand_meme(self):
        # Mad Max (1979, 5 066 votes) face a Fury Road (24 444) : un cinquieme,
        # mais c est bien le film demande. Le seuil doit le laisser passer.
        resultats = [{"id": 76341, "title": "Mad Max : Fury Road", "vote_count": 24444},
                     {"id": 9659, "title": "Mad Max", "vote_count": 5066}]
        best, _ = lookup.pick_result(resultats, "Mad Max")
        self.assertEqual(best["id"], 9659)

    def test_un_homonyme_confidentiel_ne_passe_pas(self):
        # "Le retour du roi" (1980, 223 votes) face au Seigneur des anneaux (27 139).
        resultats = [{"id": 122, "title": "Le Seigneur des anneaux : Le Retour du roi",
                      "vote_count": 27139},
                     {"id": 1361, "title": "Le retour du roi", "vote_count": 223}]
        best, _ = lookup.pick_result(resultats, "Le retour du roi")
        self.assertEqual(best["id"], 122)
    def test_sans_titre_exact_le_premier_reste(self):
        resultats = [{"id": 1, "title": "Blade II"}, {"id": 2, "title": "Blade Runner"}]
        best, _ = lookup.pick_result(resultats, "Blade")
        self.assertEqual(best["id"], 1)

    def test_le_jumeau_est_signale_meme_apres_le_choix_exact(self):
        resultats = [{"id": 414906, "title": "The Batman", "release_date": "2022-03-01",
                      "vote_count": 10238},
                     {"id": 268, "title": "Batman", "release_date": "1989-06-23",
                      "vote_count": 8107},
                     {"id": 2661, "title": "Batman", "release_date": "1966-07-30",
                      "vote_count": 512}]
        best, notes = lookup.pick_result(resultats, "Batman")
        self.assertEqual(best["id"], 268)
        self.assertIn("id 2661", notes[0])
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


class FauxFilms:
    """Client TMDB cote films : rend {requete: resultats}, retient les appels."""

    def __init__(self, reponses):
        self.reponses = reponses
        self.appels = []

    def search_movie(self, title, year=None):
        # Une cle en texte repond a la recherche sans annee ; une recherche avec
        # annee ne trouve que ce qu'on lui a explicitement prepare.
        self.appels.append((title, year))
        if year:
            return self.reponses.get((title, year), [])
        return self.reponses.get(title, [])


class TestRenfortDuDossier(unittest.TestCase):
    """Le dossier parent aide la recherche, mais ne passe jamais devant le titre."""

    def test_le_renfort_ne_passe_jamais_devant_le_titre(self):
        # "DCEU Man of Steel" ne rend rien : le resultat nu reste en place.
        tmdb = FauxFilms({"Man of Steel": [{"id": 49521, "title": "Man of Steel"}]})
        resultats, requete = lookup.search_with_context(tmdb, "Man of Steel", None, ["DCEU"])
        self.assertEqual((resultats[0]["id"], requete), (49521, "Man of Steel"))

    def test_le_dossier_vient_en_renfort(self):
        # "Folie a deux" ne dit pas de quoi il est la suite ; le dossier, si.
        tmdb = FauxFilms({"Joker Folie a deux": [{"id": 889737,
                                                  "title": "Joker : Folie a deux"}]})
        resultats, requete = lookup.search_with_context(tmdb, "Folie a deux", None, "Joker")
        self.assertEqual((resultats[0]["id"], requete), (889737, "Joker Folie a deux"))

    def test_titre_deja_ressemblant_garde_la_main(self):
        tmdb = FauxFilms({"Folie a deux": [{"id": 889737, "title": "Joker : Folie a deux"}]})
        _, requete = lookup.search_with_context(tmdb, "Folie a deux", None, "Joker")
        self.assertEqual((requete, len(tmdb.appels)), ("Folie a deux", 1))

    def test_resultat_etranger_cede_au_renfort(self):
        tmdb = FauxFilms({
            "Aube de la justice": [{"id": 1, "title": "Autre chose", "vote_count": 60}],
            "Batman Aube de la justice": [
                {"id": 209112, "title": "Batman v Superman : L'aube de la justice",
                 "vote_count": 17000}]})
        resultats, requete = lookup.search_with_context(
            tmdb, "Aube de la justice", None, "Batman")
        self.assertEqual((resultats[0]["id"], requete),
                         (209112, "Batman Aube de la justice"))

    def test_dossier_qui_repete_le_titre_ne_sert_a_rien(self):
        # Regression : "X-Men/01 - X-Men.mkv" cherchait "X-Men X-Men".
        tmdb = FauxFilms({"X-Men": [{"id": 246655, "title": "X-Men : Apocalypse"}]})
        _, requete = lookup.search_with_context(tmdb, "X-Men", None, "X-Men")
        self.assertEqual((requete, tmdb.appels), ("X-Men", [("X-Men", None)]))

    def test_dossier_contenu_dans_le_titre_non_plus(self):
        tmdb = FauxFilms({"X-Men 2": [{"id": 1, "title": "X-Men : Apocalypse"}]})
        _, requete = lookup.search_with_context(tmdb, "X-Men 2", None, "X-Men")
        self.assertEqual((requete, len(tmdb.appels)), ("X-Men 2", 1))
    def test_le_dossier_sauve_un_sous_titre_seul(self):
        # Regression : "Apocalypse" ramenait "Amour Apocalypse", un autre film,
        # avec une ressemblance assez bonne pour ne rien declencher.
        tmdb = FauxFilms({"Apocalypse": [{"id": 1, "title": "Amour Apocalypse",
                                          "vote_count": 11}],
                          "Resident Evil Apocalypse": [
                              {"id": 1576, "title": "Resident Evil : Apocalypse",
                               "vote_count": 4880}]})
        resultats, requete = lookup.search_with_context(tmdb, "Apocalypse", None,
                                                        ["Resident Evil"])
        self.assertEqual((resultats[0]["id"], requete),
                         (1576, "Resident Evil Apocalypse"))

    def test_le_renfort_doit_contenir_le_dossier_ET_le_titre(self):
        # Sinon c est un autre film de la meme saga : on ne bouge pas.
        tmdb = FauxFilms({"Apocalypse": [{"id": 1, "title": "Amour Apocalypse"}],
                          "Resident Evil Apocalypse": [
                              {"id": 2, "title": "Resident Evil : Retribution"}]})
        resultats, requete = lookup.search_with_context(tmdb, "Apocalypse", None,
                                                        ["Resident Evil"])
        self.assertEqual((resultats[0]["id"], requete), (1, "Apocalypse"))

    def test_le_grand_parent_n_est_pas_essaye(self):
        # Compromis assume : "Resident Evil/Animation/3 - Vendetta" reste mal
        # associe, mais aucun dossier de rangement ne pollue plus les recherches.
        tmdb = FauxFilms({"Vendetta": [{"id": 752, "title": "V pour Vendetta",
                                        "vote_count": 12000}],
                          "Resident Evil Vendetta": [
                              {"id": 424781, "title": "Resident Evil : Vendetta",
                               "vote_count": 3200}]})
        resultats, requete = lookup.search_with_context(
            tmdb, "Vendetta", None, ["Animation", "Resident Evil"])
        self.assertEqual((resultats[0]["id"], requete), (752, "Vendetta"))

    def test_pas_de_renfort_si_le_film_trouve_porte_deja_la_saga(self):
        # "Folie a deux" ramene deja "Joker : Folie a deux" : rien a ajouter.
        tmdb = FauxFilms({"Folie a deux": [{"id": 889737,
                                            "title": "Joker : Folie a deux"}]})
        _, requete = lookup.search_with_context(tmdb, "Folie a deux", None, ["Joker"])
        self.assertEqual((requete, len(tmdb.appels)), ("Folie a deux", 1))

    def test_un_seul_dossier_essaye(self):
        # Au-dessus du dossier immediat vivent les dossiers de rangement d une
        # mediatheque ("_Marvel"), qui ne sont pas des sagas.
        tmdb = FauxFilms({"Film": [{"id": 1, "title": "Autre", "vote_count": 900}]})
        lookup.search_with_context(tmdb, "Film", None, ["Saga", "_Marvel"])
        self.assertEqual(tmdb.appels, [("Film", None), ("Saga Film", None)])
    def test_un_petit_film_de_saga_passe_quand_meme(self):
        # "Death Race : Anarchy" (399 votes) face a "American Nightmare 2" (6 787) :
        # peu vote, mais c est bien le film du dossier.
        tmdb = FauxFilms({"Anarchy": [{"id": 238636,
                                       "title": "American Nightmare 2 : Anarchy",
                                       "vote_count": 6787}],
                          "Death Race Anarchy": [{"id": 401478,
                                                  "title": "Death Race : Anarchy",
                                                  "vote_count": 399}]})
        resultats, _ = lookup.search_with_context(tmdb, "Anarchy", None, ["Death Race"])
        self.assertEqual(resultats[0]["id"], 401478)
    def test_un_renfort_confidentiel_est_refuse(self):
        # Regression : "Les chroniques de Riddick/1 - Pitch Black" troquait Pitch
        # Black (4 914 votes) contre un court metrage de la saga (99 votes).
        tmdb = FauxFilms({"Pitch Black": [{"id": 2787, "title": "Pitch Black",
                                           "vote_count": 4914}],
                          "Les chroniques de Riddick Pitch Black": [
                              {"id": 244839,
                               "title": "Les Chroniques de Riddick : Into Pitch Black",
                               "vote_count": 99}]})
        resultats, requete = lookup.search_with_context(
            tmdb, "Pitch Black", None, ["Les chroniques de Riddick"])
        self.assertEqual((resultats[0]["id"], requete), (2787, "Pitch Black"))

    def test_un_dossier_qui_englobe_le_titre_ne_sert_a_rien(self):
        # "Les chroniques de Riddick/3 - Riddick" : le dossier redit le titre.
        tmdb = FauxFilms({"Riddick": [{"id": 87421, "title": "Riddick",
                                       "vote_count": 4492}]})
        _, requete = lookup.search_with_context(
            tmdb, "Riddick", None, ["Les chroniques de Riddick"])
        self.assertEqual((requete, len(tmdb.appels)), ("Riddick", 1))

    def test_sans_resultat_nu_le_renfort_est_pris_tel_quel(self):
        # "1.5 - Dark Fury" ne rend rien seul : le renfort ne peut pas faire pire.
        tmdb = FauxFilms({"Les chroniques de Riddick Dark Fury": [
            {"id": 14663, "title": "Les Chroniques de Riddick : Dark Fury",
             "vote_count": 300}]})
        resultats, requete = lookup.search_with_context(
            tmdb, "Dark Fury", None, ["Les chroniques de Riddick"])
        self.assertEqual(requete, "Les chroniques de Riddick Dark Fury")
        self.assertEqual(resultats[0]["vote_count"], 300)
    def test_renfort_infructueux_ne_change_rien(self):
        # Un nom de saga que TMDB ignore ("DCEU Man of Steel") ne doit rien casser.
        tmdb = FauxFilms({})
        resultats, requete = lookup.search_with_context(tmdb, "Man of Steel", None, "DCEU")
        self.assertEqual((resultats, requete), ([], "Man of Steel"))
        self.assertEqual(tmdb.appels, [("Man of Steel", None), ("DCEU Man of Steel", None)])

    def test_sans_dossier_pas_de_renfort(self):
        tmdb = FauxFilms({})
        resultats, requete = lookup.search_with_context(tmdb, "Inconnu", None)
        self.assertEqual((resultats, requete, tmdb.appels),
                         ([], "Inconnu", [("Inconnu", None)]))

    def test_seconde_tentative_sans_l_annee(self):
        tmdb = FauxFilms({"Dune": [{"id": 438631, "title": "Dune"}]})
        resultats, _ = lookup.search_with_context(tmdb, "Dune", "2021")
        self.assertEqual(resultats[0]["id"], 438631)
        self.assertEqual(tmdb.appels, [("Dune", "2021"), ("Dune", None)])

class TestVariantesDeTitre(unittest.TestCase):
    """Ce qui merite une question : le meme titre ecrit autrement."""

    def rivaux(self, requete, *titres):
        res = [{"id": i, "title": t} for i, t in enumerate(titres)]
        return [m["title"] for m in lookup.rival_versions(requete, res)]

    def test_variante_d_ecriture(self):
        # Le cas qui a motive la question : trois fiches pour un meme titre.
        self.assertEqual(
            self.rivaux("Les Quatre Fantastiques", "Les Quatre Fantastiques",
                        "Les 4 Fantastiques", "Les 4 Fantastiques"),
            ["Les 4 Fantastiques", "Les 4 Fantastiques"])

    def test_une_suite_n_est_pas_une_variante(self):
        # "Iron Man 2" rallonge le titre : c est une suite, pas une hesitation.
        self.assertEqual(self.rivaux("Iron Man", "Iron Man", "Iron Man 2", "Iron Man 3"), [])

    def test_un_titre_plus_long_n_est_pas_une_variante(self):
        # "Learie Constantine" contient la recherche, mais compte un mot de plus.
        self.assertEqual(self.rivaux("Constantine", "Constantine", "Learie Constantine"), [])
        self.assertEqual(self.rivaux("The Flash", "The Flash", "The Big Flash"), [])

    def test_titre_sans_rapport(self):
        self.assertEqual(self.rivaux("Logan", "Logan", "Casino"), [])

    def test_making_of_pris_pour_son_film(self):
        # Cas reel : TMDB classait le making-of avant le film.
        rivaux = self.rivaux("Les gardiens de la galaxie Vol 3",
                             "Rassemblement : Le making-of de Les Gardiens de la Galaxie Vol. 3",
                             "Les Gardiens de la Galaxie : Volume 3")
        self.assertEqual(rivaux, ["Les Gardiens de la Galaxie : Volume 3"])

    def test_deux_films_du_meme_titre_font_une_question(self):
        # "Dracula" en rend trois : rien dans le nom ne les departage.
        res = [{"id": 1246049, "title": "Dracula", "vote_count": 1433},
               {"id": 6114, "title": "Dracula", "vote_count": 5875}]
        best, _ = lookup.pick_result(res, "Dracula")
        self.assertEqual([m["id"] for m in lookup.twin_versions(res, best)], [6114])

    def test_un_homonyme_confidentiel_ne_fait_pas_de_question(self):
        # Sans ce filtre, un cinquieme de la mediatheque poserait une question :
        # presque tout titre a un homonyme obscur quelque part sur TMDB.
        res = [{"id": 149, "title": "Akira", "vote_count": 5000},
               {"id": 999, "title": "Akira", "vote_count": 3}]
        best, _ = lookup.pick_result(res, "Akira")
        self.assertEqual(lookup.twin_versions(res, best), [])

    def test_un_titre_different_n_est_pas_un_jumeau(self):
        res = [{"id": 1, "title": "Dracula", "vote_count": 900},
               {"id": 2, "title": "Dracula Untold", "vote_count": 6000}]
        best, _ = lookup.pick_result(res, "Dracula")
        self.assertEqual(lookup.twin_versions(res, best), [])
    def test_liste_de_choix_met_le_plus_vote_en_tete(self):
        # La reponse par defaut - une simple Entree - doit etre la plus vraisemblable.
        res = [{"id": 1, "title": "A", "vote_count": 10},
               {"id": 2, "title": "B", "vote_count": 5},
               {"id": 3, "title": "C", "vote_count": 900}]
        choix = lookup.choice_list(res, res[0], [res[2]])
        self.assertEqual([m["id"] for m in choix], [3, 1, 2])

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
