"""Etiquetage : plan d'une saison, tags produits, choix du film sur TMDB."""

import io
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import lookup, mkv, naming
from mkvlib.tmdb import TmdbError
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
        self.assertEqual(resultat, mkv.Report())
        self.assertIn("Aucun .mkv", sortie)

    def test_fichier_illisible_reste_non_bloquant(self):
        # mkvmerge refusera ce faux .mkv : le script doit le dire et continuer.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "01 - x.mkv").write_text("pas un vrai mkv", encoding="utf-8")
            report, sortie = self.process(Path(d))
        self.assertEqual((report.matched, report.total), (1, 1))
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


def sous_titre(langue, nom):
    """Piste de sous-titres minimale, telle que mkvmerge la decrit."""
    return {"type": "subtitles", "codec": "",
            "properties": {"language": langue, "track_name": nom}}

class TestFilmNonTraite(unittest.TestCase):
    """Une piste ambigue arrete tout le film, et la raison est dite."""

    AMBIGU = {"tracks": [sous_titre("eng", "English"), sous_titre("eng", "English")]}
    SAIN = {"tracks": [sous_titre("fre", "Français forcé"),
                       sous_titre("fre", "Français complet")]}

    def process(self, *infos):
        """Lance process_movie sur un film d un fichier par info donnee."""
        args = types.SimpleNamespace(verify=False, apply=False, skip_done=False)
        chemins = [Path(f"cd{i}.mkv") for i, _ in enumerate(infos, 1)]
        entry = naming.MovieFolder(folder=Path("."), files=chemins, rawname="Film")
        lectures = {p: mkv.Reading(info=info) for p, info in zip(chemins, infos)}
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            report = films.process_movie(entry, {"title": "Film"}, lectures,
                                         args, OPTS, None)
        return report, sortie.getvalue()

    def test_film_ambigu_laisse_intact(self):
        report, sortie = self.process(self.AMBIGU)
        self.assertEqual((report.matched, report.total, report.skipped), (1, 1, 1))
        self.assertIn("[NON TRAITE]", sortie)
        self.assertIn("st s1 et st s2 [en]", sortie)
        self.assertNotIn(" -> ", sortie)          # aucun renommage n a ete prepare

    def test_film_sain_traite_normalement(self):
        report, sortie = self.process(self.SAIN)
        self.assertEqual(report.skipped, 0)
        self.assertIn("+Forced", sortie)

    def test_un_seul_fichier_ambigu_bloque_le_film_entier(self):
        # A moitie etiquete, le film aurait l air fait : le souci passerait a la
        # trappe au passage suivant.
        report, sortie = self.process(self.SAIN, self.AMBIGU)
        self.assertEqual(report.skipped, 1)
        self.assertIn("cd2.mkv : st s1 et st s2", sortie)
        self.assertNotIn("+Forced", sortie)       # cd1.mkv non plus n a ete prepare

    def test_le_bilan_le_compte_et_le_signale(self):
        report = mkv.Report(matched=1, total=1) + mkv.Report(matched=1, total=1, skipped=1)
        self.assertEqual((report.total, report.skipped), (2, 1))
        self.assertIn("1 non traite(s)", report.epilogue())
        self.assertEqual(report.exit_code, 1)

class FauxTmdbFilms:
    """Rend une fiche par id, et une recherche fixe. Note ce qui a ete demande."""

    def __init__(self, resultats=()):
        self.resultats = list(resultats)
        self.details = []

    def search_movie(self, title, year=None):
        return list(self.resultats)

    def movie(self, movie_id, language=None):
        self.details.append(int(movie_id))
        return {"id": int(movie_id), "title": f"Film {movie_id}"}

    def local_release_date(self, movie_id, region):
        return None


class TestQuestionsDeFinDePassage(unittest.TestCase):
    """Une association douteuse attend la fin du passage, et une reponse."""

    CANDIDATS = [{"id": 22059, "title": "Les Quatre Fantastiques",
                  "release_date": "1994-01-01"},
                 {"id": 9738, "title": "Les 4 Fantastiques",
                  "release_date": "2005-07-06"}]

    def setUp(self):
        self.args = types.SimpleNamespace(no_tag=True, artwork=False, apply=False,
                                          verify=False, skip_done=False, no_ask=False,
                                          language="fr-FR", no_date=True, tmdb_id=None)

    def attente(self, combien=1):
        sortie = []
        for i in range(combien):
            entry = naming.MovieFolder(folder=Path("."), files=[Path(f"f{i}.mkv")],
                                       rawname=f"Les Quatre Fantastiques {i}")
            sortie.append((entry, lookup.Doubt(query="Les Quatre Fantastiques",
                                               candidates=list(self.CANDIDATS))))
        return sortie

    def poser(self, reponses, combien=1, interactif=True):
        """Joue resolve_pending avec des reponses prefabriquees."""
        tmdb = FauxTmdbFilms()
        library = films.Library([], {}, {})
        restantes, demandes = list(reponses), []

        def faux_choix(nombre, defaut=0):
            demandes.append(nombre)
            return restantes.pop(0)

        sortie = io.StringIO()
        with mock.patch.object(films.cli, "can_ask", lambda: interactif):
            with mock.patch.object(films.cli, "ask_choice", faux_choix):
                with redirect_stdout(sortie):
                    report = films.resolve_pending(self.attente(combien), library,
                                                   self.args, OPTS, tmdb)
        return report, sortie.getvalue(), tmdb, demandes

    def test_la_reponse_choisit_la_fiche(self):
        report, sortie, tmdb, _ = self.poser([1])
        self.assertEqual(tmdb.details, [9738])          # la 2e, pas la 1re
        self.assertEqual((report.matched, report.pending), (1, 0))
        self.assertIn("tmdbid-9738", sortie)            # comment ne plus la poser

    def test_entree_vide_garde_le_defaut(self):
        _, _, tmdb, _ = self.poser([0])
        self.assertEqual(tmdb.details, [22059])

    def test_ignorer_laisse_le_film_intact(self):
        report, sortie, tmdb, _ = self.poser([films.cli.ASK_SKIP])
        self.assertEqual(tmdb.details, [])
        self.assertEqual((report.matched, report.pending), (1, 1))
        self.assertIn("[NON TRAITE]", sortie)

    def test_arreter_saute_toutes_les_suivantes(self):
        report, _, tmdb, demandes = self.poser([films.cli.ASK_STOP], combien=3)
        self.assertEqual(len(demandes), 1)              # une seule question posee
        self.assertEqual((report.pending, tmdb.details), (3, []))

    def test_terminal_non_interactif_ne_bloque_pas(self):
        # Sortie redirigee : la question ne serait vue par personne.
        report, sortie, tmdb, demandes = self.poser([], combien=2, interactif=False)
        self.assertEqual((demandes, tmdb.details), ([], []))
        self.assertEqual((report.total, report.pending), (2, 2))
        self.assertIn("non interactif", sortie)

    def resoudre(self):
        tmdb = FauxTmdbFilms(self.CANDIDATS)
        with redirect_stdout(io.StringIO()):
            movie, doute = films.resolve_movie("Les Quatre Fantastiques", self.args,
                                               tmdb, single=False)
        return movie, doute, tmdb

    def test_no_ask_garde_le_premier_resultat(self):
        # L echappatoire pour les scripts : le comportement d avant.
        self.args.no_ask = True
        movie, doute, _ = self.resoudre()
        self.assertIsNone(doute)
        self.assertEqual(movie["id"], 22059)

    def test_sans_no_ask_la_question_est_mise_de_cote(self):
        movie, doute, tmdb = self.resoudre()
        self.assertIsNone(movie)                        # pas encore charge
        self.assertEqual(tmdb.details, [])              # ni meme interroge
        self.assertEqual([c["id"] for c in doute.candidates], [22059, 9738])

class TestRecap(unittest.TestCase):
    SAISON = {"season_number": 1, "name": "Saison 1", "episodes": [
        {"episode_number": 1, "name": "Un", "air_date": "2024-01-02"},
        {"episode_number": 2, "name": "Deux"},
        {"episode_number": 3, "name": "Trois"},
    ]}

    def rendre(self, owned):
        run = series.SeasonRun(Path("."), 1, self.SAISON, owned)
        return series.build_recap_html("Ma Serie", {"overview": "Resume"},
                                       [run], "1234", {}, "w300")

    def test_episodes_absents_marques(self):
        html = self.rendre({1})
        self.assertEqual(html.count("class='miss'"), 2)
        self.assertEqual(html.count("class='ep absent'"), 2)
        self.assertIn("<span class='cnt'>1/3</span>", html)

    def test_saison_complete_sans_marquage(self):
        html = self.rendre({1, 2, 3})
        self.assertNotIn("class='miss'", html)
        self.assertIn("<span class='cnt'>3/3</span>", html)

    def test_sans_inventaire_rien_n_est_juge(self):
        # Aucun fichier repere : tout declarer manquant serait un mensonge.
        html = self.rendre(set())
        self.assertNotIn("class='miss'", html)
        self.assertNotIn("class='cnt'", html)

    def test_page_complete(self):
        html = self.rendre({1})
        self.assertIn("<title>Ma Serie</title>", html)
        self.assertIn("<meta name='tmdb-id' content='1234'>", html)
        self.assertEqual(html.count("<div class='ep"), 3)
        self.assertIn("2 janvier 2024", html)

    def test_titres_echappes(self):
        saison = dict(self.SAISON, episodes=[{"episode_number": 1, "name": "Tom & <b>Jerry</b>"}])
        run = series.SeasonRun(Path("."), 1, saison, {1})
        html = series.build_recap_html("S", {}, [run], "1", {}, "w300")
        self.assertIn("Tom &amp; &lt;b&gt;Jerry&lt;/b&gt;", html)
        self.assertNotIn("<b>Jerry</b>", html)

    def test_vignettes_integrees_et_manquantes(self):
        saison = dict(self.SAISON, episodes=[
            {"episode_number": 1, "name": "Un", "still_path": "/a.jpg"},
            {"episode_number": 2, "name": "Deux"}])
        run = series.SeasonRun(Path("."), 1, saison, {1, 2})
        html = series.build_recap_html("S", {}, [run], "1",
                                       {"w300/a.jpg": "data:image/jpeg;base64,AAA"}, "w300")
        self.assertIn("<img data-img='w300/a.jpg' src='data:image/jpeg;base64,AAA'", html)
        self.assertIn("<div class='noimg'></div>", html)


class FauxTmdb:
    """Renvoie des sagas preparees, et compte les appels."""

    def __init__(self, sagas, echecs=()):
        self.sagas = sagas
        self.echecs = set(echecs)
        self.appels = []

    def collection(self, ident, language=None):
        self.appels.append(ident)
        if ident in self.echecs:
            raise TmdbError("indisponible")
        return self.sagas[ident]


IRON = {"id": 1, "name": "Iron Man - Saga", "parts": [
    {"id": 11, "title": "Iron Man", "release_date": "2008-04-30", "poster_path": "/a.jpg"},
    {"id": 12, "title": "Iron Man 2", "release_date": "2010-04-28", "poster_path": "/b.jpg"},
    {"id": 13, "title": "Iron Man 3", "release_date": "2013-04-24"},
]}


def film(ident, titre, saga=None, **extra):
    movie = {"id": ident, "title": titre, "release_date": "2008-04-30",
             "runtime": 126, "poster_path": "/a.jpg"}
    if saga:
        movie["belongs_to_collection"] = {"id": saga["id"], "name": saga["name"]}
    movie.update(extra)
    return movie


class TestRecapFilms(unittest.TestCase):
    def test_une_requete_par_saga(self):
        tmdb = FauxTmdb({1: IRON})
        sagas = films.fetch_collections([film(11, "Iron Man", IRON),
                                         film(13, "Iron Man 3", IRON),
                                         film(99, "Heat")], tmdb)
        self.assertEqual(tmdb.appels, [1])          # la saga n'est demandee qu'une fois
        self.assertEqual(list(sagas), [1])

    def test_saga_indisponible_ignoree(self):
        tmdb = FauxTmdb({1: IRON}, echecs={1})
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            sagas = films.fetch_collections([film(11, "Iron Man", IRON)], tmdb)
        self.assertEqual(sagas, {})
        self.assertIn("ignoree", sortie.getvalue())

    def test_saga_incomplete_montre_les_manquants(self):
        sections = films.library_sections([film(11, "Iron Man", IRON)], {1: IRON})
        titre, cards = sections[0]
        self.assertEqual(titre, "Iron Man - Saga")
        self.assertEqual([(c.title, c.owned) for c in cards],
                         [("Iron Man", True), ("Iron Man 2", False), ("Iron Man 3", False)])

    def test_films_hors_saga_a_la_fin(self):
        sections = films.library_sections(
            [film(11, "Iron Man", IRON), film(99, "Heat"), film(98, "Alien")], {1: IRON})
        self.assertEqual([t for t, _ in sections], ["Iron Man - Saga", "Hors saga"])
        self.assertEqual([c.title for c in sections[-1][1]], ["Alien", "Heat"])

    def test_saga_non_chargee_bascule_hors_saga(self):
        sections = films.library_sections([film(11, "Iron Man", IRON)], {})
        self.assertEqual([t for t, _ in sections], ["Hors saga"])

    def test_donnees_du_film_possede_prioritaires(self):
        # Le film qu'on possede apporte sa duree et son synopsis, pas la fiche de saga.
        possede = film(11, "Iron Man", IRON, runtime=126, overview="Tony Stark")
        cards = films.library_sections([possede], {1: IRON})[0][1]
        self.assertEqual((cards[0].runtime, cards[0].overview), (126, "Tony Stark"))
        self.assertIsNone(cards[1].runtime)


class TestRenduRecapFilms(unittest.TestCase):
    def rendre(self, sections, posters=None):
        return films.build_recap_html("Films", sections, posters or {}, "w185")

    def test_compteur_seulement_si_il_manque_quelque_chose(self):
        complete = [films.Card("A", "2008-01-01"), films.Card("B", "2010-01-01")]
        self.assertNotIn("class='cnt'", self.rendre([("Saga", complete)]))
        incomplete = [films.Card("A", "2008-01-01"), films.Card("B", owned=False)]
        self.assertIn("<span class='cnt'>1/2</span>", self.rendre([("Saga", incomplete)]))

    def test_film_manquant_marque(self):
        html = self.rendre([("Saga", [films.Card("Iron Man 2", "2010-04-28", owned=False)])])
        self.assertIn("class='film absent'", html)
        self.assertIn(">manquant</div>", html)
        self.assertIn("2010", html)

    def test_resume_de_la_mediatheque(self):
        sections = [("Saga", [films.Card("A"), films.Card("B", owned=False)]),
                    ("Hors saga", [films.Card("C")])]
        html = self.rendre(sections)
        self.assertIn("2 film(s) · 1 saga(s) · 1 manquant(s) dans les sagas", html)

    def test_affiche_absente_remplacee(self):
        html = self.rendre([("Hors saga", [films.Card("Sans affiche")])])
        self.assertIn("<div class='noimg'></div>", html)

    def test_affiche_integree(self):
        card = films.Card("Iron Man", "2008-04-30", poster="/a.jpg")
        html = self.rendre([("Saga", [card])], {"w185/a.jpg": "data:image/jpeg;base64,AAA"})
        self.assertIn("<img data-img='w185/a.jpg' src='data:image/jpeg;base64,AAA'", html)

    def test_titres_et_synopsis_echappes(self):
        card = films.Card("Tom & Jerry", overview="Un chat & <b>une souris</b>")
        html = self.rendre([("Hors saga", [card])])
        self.assertIn("Tom &amp; Jerry", html)
        self.assertIn("&lt;b&gt;une souris&lt;/b&gt;", html)
        self.assertNotIn("<b>une souris</b>", html)


if __name__ == "__main__":
    unittest.main()
