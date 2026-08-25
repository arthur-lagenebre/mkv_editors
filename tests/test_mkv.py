"""Noms de pistes, tags XML et comparaison a l'etat vise (aucun outil externe requis)."""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import mkv


def piste(kind, **props):
    return {"type": kind, "codec": props.pop("codec", ""), "properties": props}


class TestNomsDePistes(unittest.TestCase):
    def test_audio_codec_canaux_debit(self):
        tr = piste("audio", codec="E-AC-3", audio_channels=6)
        tr["_bitrate_kbps"] = 640
        self.assertEqual(mkv.audio_track_name(tr), "E-AC-3 5.1 640 kb/s")

    def test_audio_sans_debit_connu(self):
        self.assertEqual(mkv.audio_track_name(piste("audio", codec="AAC", audio_channels=2)),
                         "AAC 2.0")

    def test_audio_canaux_inhabituels(self):
        self.assertEqual(mkv.audio_track_name(piste("audio", codec="DTS", audio_channels=12)),
                         "DTS 12ch")

    def test_sous_titres_sans_drapeau(self):
        self.assertEqual(mkv.subtitle_track_name(piste("subtitles")), "Full")

    def test_sous_titres_drapeaux_actifs(self):
        tr = piste("subtitles", forced_track=True, flag_hearing_impaired=True)
        self.assertEqual(mkv.subtitle_track_name(tr), "Forced SDH")

    def test_selecteurs_numerotes_par_type(self):
        info = {"tracks": [piste("video"), piste("audio"), piste("subtitles"),
                           piste("audio"), piste("subtitles")]}
        audios, subs = mkv.track_selectors(info)
        self.assertEqual([s for s, _ in audios], ["a1", "a2"])
        self.assertEqual([s for s, _ in subs], ["s1", "s2"])

    def test_piste_par_defaut_est_la_francaise(self):
        info = {"tracks": [piste("audio", language="eng"), piste("audio", language="fre")]}
        audios, _ = mkv.track_selectors(info)
        self.assertEqual(mkv.primary_audio_sel(audios), "a2")

    def test_piste_par_defaut_a_defaut_la_premiere(self):
        info = {"tracks": [piste("audio", language="eng"), piste("audio", language="jpn")]}
        audios, _ = mkv.track_selectors(info)
        self.assertEqual(mkv.primary_audio_sel(audios), "a1")


FORCE, SDH = "forced_track", "flag_hearing_impaired"


class TestDrapeauxDeduitsDuNom(unittest.TestCase):
    """Quand le nom dit ce que les drapeaux taisent, on pose le drapeau."""

    opts = mkv.Options(cover=False, date=False, stats=False)

    def subs(self, *pistes):
        return mkv.track_selectors({"tracks": list(pistes)})[1]

    def deduits(self, subs):
        """{selecteur: drapeaux a poser} tels que le script les calcule."""
        return {sel: aj for sel, _, aj in mkv.subtitle_targets(subs, self.opts) if aj}

    def test_nom_force_pose_le_drapeau(self):
        # Regression : la piste devenait "Full" a cote de la piste complete,
        # elle aussi "Full" - deux noms identiques, l'information perdue.
        subs = self.subs(piste("subtitles", language="fre", track_name="Français forcé"),
                         piste("subtitles", language="fre", track_name="Français complet"))
        self.assertEqual(self.deduits(subs), {"s1": {FORCE}})
        self.assertEqual(mkv.subtitle_track_name(subs[0][1], {FORCE}), "Forced")
        self.assertEqual(mkv.subtitle_track_name(subs[1][1]), "Full")

    def test_nom_sdh_pose_le_drapeau(self):
        # Meme defaut, symetrique : "English SDH" sans le drapeau malentendant
        # devenait "Full" a cote de "English full", lui aussi "Full".
        subs = self.subs(piste("subtitles", language="eng", track_name="English full"),
                         piste("subtitles", language="eng", track_name="English SDH"))
        self.assertEqual(self.deduits(subs), {"s2": {SDH}})
        self.assertEqual(mkv.subtitle_track_name(subs[1][1], {SDH}), "SDH")

    def test_malentendant_ecrit_en_toutes_lettres(self):
        subs = self.subs(piste("subtitles", language="fre",
                               track_name="Français malentendants"))
        self.assertEqual(self.deduits(subs), {"s1": {SDH}})

    def test_les_deux_drapeaux_a_la_fois(self):
        subs = self.subs(piste("subtitles", language="fre",
                               track_name="Français forcé SDH"))
        self.assertEqual(self.deduits(subs), {"s1": {FORCE, SDH}})
        self.assertEqual(mkv.subtitle_track_name(subs[0][1], {FORCE, SDH}), "Forced SDH")

    def test_rien_dans_une_langue_qui_declare_deja_le_drapeau(self):
        # La ou il existe, la situation est declaree : un nom ne la contredit
        # pas, et un second forced ferait choisir le lecteur au hasard.
        subs = self.subs(piste("subtitles", language="fre", forced_track=True,
                               track_name="forced colored"),
                         piste("subtitles", language="fre", track_name="Français forcé"))
        self.assertEqual(self.deduits(subs), {})

    def test_mais_une_autre_langue_reste_libre(self):
        # Une piste forcee anglaise ne dit rien de la francaise.
        subs = self.subs(piste("subtitles", language="eng", forced_track=True,
                               track_name="English forced"),
                         piste("subtitles", language="fre", track_name="Français forcé"))
        self.assertEqual(self.deduits(subs), {"s2": {FORCE}})

    def test_une_seule_piste_par_langue(self):
        subs = self.subs(piste("subtitles", language="fre", track_name="Français forcé"),
                         piste("subtitles", language="fre", track_name="forcé colored"))
        self.assertEqual(self.deduits(subs), {"s1": {FORCE}})

    def test_nom_muet_ne_deduit_rien(self):
        subs = self.subs(piste("subtitles", language="fre", track_name="Français complet"),
                         piste("subtitles", language="eng", track_name="English full"))
        self.assertEqual(self.deduits(subs), {})

    def test_no_flags_s_abstient(self):
        subs = self.subs(piste("subtitles", language="fre", track_name="Français forcé"))
        sans = mkv.Options(cover=False, date=False, stats=False, flags=False)
        self.assertEqual([aj for _, _, aj in mkv.subtitle_targets(subs, sans)], [set()])
        self.assertEqual(self.deduits(subs), {"s1": {FORCE}})

    def test_verify_reclame_le_drapeau(self):
        # Sans cet ecart, --skip-done sauterait le fichier a corriger.
        info = {"container": {"properties": {"title": "X"}},
                "tracks": [piste("subtitles", language="eng", track_name="English SDH")]}
        diffs = [lbl for lbl, ok, _ in mkv.verify(info, mkv.Target(title="X"), self.opts)
                 if not ok]
        self.assertIn("st s1 sdh", diffs)

    def test_ecriture_pose_les_drapeaux_et_le_nom(self):
        vu = {}

        def run(cmd, **kwargs):
            vu["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        info = {"tracks": [piste("subtitles", language="eng",
                                 track_name="English full SDH")]}
        with mock.patch.object(mkv.subprocess, "run", run):
            code, _ = mkv.write("film.mkv", info, mkv.Target(title="X", tags_xml="<Tags/>"),
                                self.opts, None)
        self.assertEqual(code, 0)
        self.assertIn("flag-hearing-impaired=1", vu["cmd"])
        self.assertIn("name=SDH", vu["cmd"])

    def test_second_passage_ne_change_plus_rien(self):
        # Idempotence : le drapeau pose, le nom en decoule de lui-meme.
        info = {"container": {"properties": {"title": "X"}},
                "tracks": [piste("subtitles", language="fre", forced_track=True,
                                 track_name="Forced")]}
        self.assertTrue(mkv.is_conform(info, mkv.Target(title="X"), self.opts))

class TestPistesAmbigues(unittest.TestCase):
    """Ce qui fait rendre la main : une information que le renommage effacerait."""

    opts = mkv.Options(cover=False, date=False, stats=False)

    def conflits(self, *pistes, opts=None):
        return mkv.track_conflicts({"tracks": list(pistes)}, opts or self.opts)

    def test_forced_non_transposable_et_noms_en_double(self):
        # L etat exact d Aquaman : une piste forcee declaree, une deuxieme qui
        # ne l est que par son nom, et la complete. Les deux dernieres
        # deviendraient "Full" toutes les deux.
        raisons = self.conflits(
            piste("subtitles", language="fre", forced_track=True, track_name="forced colored"),
            piste("subtitles", language="fre", track_name="Français forcé"),
            piste("subtitles", language="fre", track_name="Français complet"))
        self.assertEqual(len(raisons), 2)
        self.assertIn("st s2 : le nom l'annonce", raisons[0])
        self.assertIn("st s1 porte deja le drapeau", raisons[0])
        self.assertIn("st s2 et st s3 [fr]", raisons[1])
        self.assertIn("Full", raisons[1])

    def test_deux_sous_titres_indistinguables(self):
        # L etat exact de Catwoman : deux pistes anglaises que rien ne separe.
        raisons = self.conflits(piste("subtitles", language="eng", track_name="English"),
                                piste("subtitles", language="eng", track_name="English"))
        self.assertEqual(len(raisons), 1)
        self.assertIn("st s1 et st s2 [en]", raisons[0])

    def test_deux_audio_de_meme_langue_et_meme_qualite(self):
        raisons = self.conflits(
            piste("audio", language="fre", codec="AC-3", audio_channels=6),
            piste("audio", language="fre", codec="AC-3", audio_channels=6))
        self.assertIn("audio a1 et audio a2 [fr]", raisons[0])

    def test_langues_differentes_ne_se_marchent_pas_dessus(self):
        self.assertEqual(self.conflits(
            piste("audio", language="fre", codec="AC-3", audio_channels=6),
            piste("audio", language="eng", codec="AC-3", audio_channels=6),
            piste("subtitles", language="fre", track_name="Français complet"),
            piste("subtitles", language="eng", track_name="English full")), [])

    def test_fichier_sain(self):
        # Le cas courant : un force sans drapeau (qu on posera) et une complete.
        self.assertEqual(self.conflits(
            piste("subtitles", language="fre", track_name="Français forcé"),
            piste("subtitles", language="fre", track_name="Français complet")), [])

    def test_no_flags_rend_le_nom_force_intransposable(self):
        sans = mkv.Options(cover=False, date=False, stats=False, flags=False)
        raisons = self.conflits(piste("subtitles", language="fre",
                                      track_name="Français forcé"), opts=sans)
        self.assertIn("--no-flags", raisons[0])

    def test_sans_renommage_rien_a_signaler(self):
        muet = mkv.Options(cover=False, date=False, stats=False,
                           audio_names=False, sub_names=False)
        self.assertEqual(self.conflits(
            piste("subtitles", language="eng", track_name="English"),
            piste("subtitles", language="eng", track_name="English"), opts=muet), [])

    def test_fichier_illisible(self):
        self.assertEqual(mkv.track_conflicts(None, self.opts), [])

class TestIdentifiantDansLeFichier(unittest.TestCase):
    """L identifiant TMDB inscrit dans le .mkv : ecrit, relu, et pas paye pour rien."""

    def test_valeur_au_format_matroska(self):
        # Matroska normalise ce tag : "movie/1234", pas "1234".
        self.assertEqual(mkv.tmdb_value(314), "movie/314")

    def test_relecture(self):
        self.assertEqual(mkv.tmdb_id({(50, "TMDB", "movie/314")}), "314")

    def test_relecture_d_un_identifiant_nu(self):
        # Tolere la forme sans prefixe, qu ecrivent d autres outils.
        self.assertEqual(mkv.tmdb_id({(50, "TMDB", "314")}), "314")

    def test_un_identifiant_de_serie_n_est_pas_un_film(self):
        self.assertIsNone(mkv.tmdb_id({(50, "TMDB", "tv/1396")}))

    def test_sans_identifiant(self):
        self.assertIsNone(mkv.tmdb_id({(50, "TITLE", "Catwoman")}))
        self.assertIsNone(mkv.tmdb_id(None))

    def test_presence_de_tags_annoncee_par_mkvmerge(self):
        self.assertTrue(mkv.has_tags({"global_tags": [{"num_entries": 2}]}))
        self.assertFalse(mkv.has_tags({"global_tags": []}))
        self.assertFalse(mkv.has_tags(None))

    def lire(self, info):
        """Lance inspect en notant si mkvextract a ete appele."""
        appels = []
        with mock.patch.object(mkv, "identify", lambda p: (info, "")):
            with mock.patch.object(mkv, "read_tags",
                                   lambda p: appels.append(p) or {(50, "TMDB", "movie/9")}):
                lecture = mkv.inspect("film.mkv", with_probe=False)
        return lecture, appels

    def test_aucune_relecture_si_le_fichier_n_a_pas_de_tags(self):
        # Le cout du feature : un mkvextract par fichier. Sur une mediatheque
        # jamais etiquetee, il ne doit jamais etre lance.
        lecture, appels = self.lire({"global_tags": [], "tracks": []})
        self.assertEqual(appels, [])
        self.assertIsNone(lecture.tags)

    def test_relecture_quand_le_fichier_declare_des_tags(self):
        lecture, appels = self.lire({"global_tags": [{"num_entries": 3}], "tracks": []})
        self.assertEqual(len(appels), 1)
        self.assertEqual(mkv.tmdb_id(lecture.tags), "9")

class TestTagsXML(unittest.TestCase):
    def test_document_bien_forme_et_echappe(self):
        xml = mkv.tags_document([mkv.tag_block(50, [mkv.simple("TITLE", "Rock & <Roll>")])])
        root = ElementTree.fromstring(xml)
        self.assertEqual(root.tag, "Tags")
        self.assertEqual(root.find("./Tag/Simple/String").text, "Rock & <Roll>")
        self.assertEqual(root.find("./Tag/Targets/TargetTypeValue").text, "50")

    def test_credits_sans_doublon_et_plafonnes(self):
        crew = [{"name": "A", "job": "Director", "department": "Directing"},
                {"name": "A", "job": "Director", "department": "Directing"},
                {"name": "B", "department": "Writing"}]
        cast = [{"name": f"Acteur{i}", "character": f"Role{i}"} for i in range(5)]
        lines = mkv.credits_lines(crew, cast, max_actors=2)
        self.assertEqual(sum("DIRECTOR" in x for x in lines), 1)
        self.assertEqual(sum("WRITTEN_BY" in x for x in lines), 1)
        self.assertEqual(sum("ACTOR" in x for x in lines), 2)
        self.assertIn("Acteur0 (Role0)", lines[2])


class TestVerification(unittest.TestCase):
    def setUp(self):
        self.opts = mkv.Options(cover=True, date=True, audio_names=True,
                                sub_names=True, flags=True, stats=True, image_size="w780")
        self.info = {"container": {"properties": {"title": "Pilote", "date_utc": "2024-01-02T00:00:00Z"}},
                     "tracks": [piste("audio", codec="AAC", audio_channels=2, track_name="AAC 2.0")],
                     "attachments": [{"file_name": "cover.jpg"}]}

    def test_fichier_conforme(self):
        target = mkv.Target(title="Pilote", date="2024-01-02", poster="/a.jpg")
        self.assertTrue(mkv.is_conform(self.info, target, self.opts))

    def test_titre_different_signale(self):
        target = mkv.Target(title="Autre", date="2024-01-02", poster="/a.jpg")
        diffs = [lbl for lbl, ok, _ in mkv.verify(self.info, target, self.opts) if not ok]
        self.assertEqual(diffs, ["titre"])

    def test_pas_de_jaquette_exigee_si_tmdb_n_en_a_pas(self):
        # Sans still_path cote TMDB, exiger une jaquette rendrait --skip-done inutile.
        info = dict(self.info, attachments=[])
        target = mkv.Target(title="Pilote", date="2024-01-02", poster=None)
        self.assertTrue(mkv.is_conform(info, target, self.opts))

    def test_options_depuis_les_drapeaux(self):
        class Args:
            no_cover = True
            no_date = False
            no_audio_names = False
            no_sub_names = True
            no_flags = False
            no_stats = True
            image_size = "w300"
        opts = mkv.Options.from_args(Args())
        self.assertEqual((opts.cover, opts.sub_names, opts.stats, opts.image_size),
                         (False, False, False, "w300"))


class TestOutilsExternes(unittest.TestCase):
    """Comment la sortie de mkvmerge et de ffprobe est lue."""

    @staticmethod
    def _faux_run(stdout, stderr=""):
        def run(cmd, **kwargs):
            run.vu = kwargs
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=stderr)
        run.vu = {}
        return run

    def test_sortie_decodee_en_utf8(self):
        # Regression : sans encodage explicite, Python decode en cp1252 et un
        # caractere absent de cette table (le trait d'union de "est-ce", U+2010)
        # fait echouer la lecture dans un thread de subprocess.
        faux = self._faux_run('{"tracks": []}')
        with mock.patch.object(mkv.subprocess, "run", faux):
            mkv.identify("film.mkv")
        self.assertEqual(faux.vu.get("encoding"), "utf-8")
        self.assertEqual(faux.vu.get("errors"), "replace")

    def test_sortie_vide_ne_plante_pas(self):
        # C'est ainsi que se manifeste un echec de decodage : stdout a None,
        # code de retour 0, aucune exception.
        faux = self._faux_run(None, None)
        with mock.patch.object(mkv.subprocess, "run", faux):
            info, note = mkv.identify("film.mkv")
            self.assertIsNone(info)
            self.assertIn("sortie vide", note)
            self.assertEqual(mkv.probe("film.mkv"), mkv.Probe())

    def test_json_inattendu_ignore(self):
        faux = self._faux_run("[]")
        with mock.patch.object(mkv.subprocess, "run", faux):
            self.assertEqual(mkv.probe("film.mkv"), mkv.Probe())

    def test_outil_absent_pendant_l_ecriture(self):
        # Regression : la disparition de mkvpropedit remontait en pile d'appels
        # jusqu'a l'utilisateur, au lieu d'etre comptee comme un echec d'ecriture.
        def absent(cmd, **kwargs):
            raise FileNotFoundError(2, "introuvable", "mkvpropedit")

        with mock.patch.object(mkv.subprocess, "run", absent):
            code, msg = mkv.write("film.mkv", {"tracks": []},
                                  mkv.Target(title="X", tags_xml="<Tags/>"),
                                  mkv.Options(cover=False, stats=False, flags=False), None)
        self.assertEqual(code, 1)
        self.assertIn("mkvpropedit", msg)

    def test_message_d_ecriture_sans_sortie(self):
        faux = self._faux_run(None, None)
        target = mkv.Target(title="X", tags_xml="<Tags/>")
        opts = mkv.Options(cover=False, stats=False, flags=False)
        with mock.patch.object(mkv.subprocess, "run", faux):
            code, msg = mkv.write("film.mkv", {"tracks": []}, target, opts, None)
        self.assertEqual((code, msg), (0, ""))


class TestReport(unittest.TestCase):
    def test_addition(self):
        total = mkv.Report(1, 1) + mkv.Report(2, 3, diffs=1) + mkv.Report(failures=2)
        self.assertEqual((total.matched, total.total, total.diffs, total.failures),
                         (3, 4, 1, 2))

    def test_code_de_sortie_nul_quand_tout_va_bien(self):
        self.assertEqual(mkv.Report(matched=5, total=5).exit_code, 0)

    def test_code_de_sortie_non_nul(self):
        # --verify doit pouvoir servir dans un script.
        self.assertEqual(mkv.Report(diffs=1).exit_code, 1)
        self.assertEqual(mkv.Report(failures=1).exit_code, 1)

    def test_epilogue(self):
        self.assertEqual(mkv.Report().epilogue(), "")
        self.assertEqual(mkv.Report(diffs=2).epilogue(), "2 fichier(s) non conforme(s)")
        self.assertEqual(mkv.Report(diffs=1, failures=3).epilogue(),
                         "1 fichier(s) non conforme(s) ; 3 ecriture(s) en echec")


class TestLectureParallele(unittest.TestCase):
    def test_inspect_rend_info_probe_et_remarque(self):
        faux = TestOutilsExternes._faux_run('{"tracks": []}')
        with mock.patch.object(mkv.subprocess, "run", faux):
            lecture = mkv.inspect("film.mkv", with_probe=False)
        self.assertEqual((lecture.info, lecture.note), ({"tracks": []}, ""))
        self.assertEqual(lecture.probe, mkv.Probe())
        self.assertIsNone(lecture.tags)          # non demandes, donc non relus

    def test_inspect_all_couvre_tous_les_fichiers(self):
        faux = TestOutilsExternes._faux_run('{"tracks": []}')
        fichiers = [f"e{n}.mkv" for n in range(5)]
        with mock.patch.object(mkv.subprocess, "run", faux):
            lectures = mkv.inspect_all(fichiers, with_probe=False)
        self.assertEqual(sorted(lectures), sorted(fichiers))
        self.assertTrue(all(l.info == {"tracks": []} for l in lectures.values()))

    def test_inspect_all_sans_fichier(self):
        self.assertEqual(mkv.inspect_all([]), {})

    def test_remarques_rendues_et_non_imprimees(self):
        # C'est ce qui rend la lecture parallelisable sans entrelacer l'affichage.
        faux = TestOutilsExternes._faux_run(None, None)
        sortie = io.StringIO()
        with mock.patch.object(mkv.subprocess, "run", faux), redirect_stdout(sortie):
            lecture = mkv.inspect("film.mkv")
        self.assertNotEqual(lecture.note, "")
        self.assertEqual(sortie.getvalue(), "")




TAGS_ECRITS = """<?xml version="1.0"?>
<Tags>
  <Tag>
    <Targets><TargetTypeValue>70</TargetTypeValue></Targets>
    <Simple><Name>TITLE</Name><String>Iron Man - Saga</String></Simple>
  </Tag>
  <Tag>
    <Targets />
    <Simple><Name>TITLE</Name><String>Iron Man</String>
      <TagLanguageIETF>und</TagLanguageIETF></Simple>
    <Simple><Name>ACTOR</Name><String>R. Downey Jr.</String></Simple>
  </Tag>
  <Tag>
    <Targets><TrackUID>112689479983</TrackUID></Targets>
    <Simple><Name>BPS</Name><String>128000</String></Simple>
  </Tag>
</Tags>"""


class TestLectureDesTags(unittest.TestCase):
    def test_cible_absente_vaut_cinquante(self):
        # mkvpropedit omet TargetTypeValue quand il vaut 50, la valeur par defaut :
        # sans cette equivalence, un fichier tout juste ecrit paraitrait different.
        tags = mkv.parse_tags(TAGS_ECRITS)
        self.assertIn((50, "TITLE", "Iron Man"), tags)
        self.assertIn((70, "TITLE", "Iron Man - Saga"), tags)

    def test_tags_de_piste_ecartes(self):
        # Les statistiques de piste ne viennent pas de TMDB.
        self.assertNotIn((50, "BPS", "128000"), mkv.parse_tags(TAGS_ECRITS))
        self.assertEqual(len(mkv.parse_tags(TAGS_ECRITS)), 3)

    def test_bom_et_xml_casse(self):
        self.assertEqual(mkv.parse_tags("﻿" + TAGS_ECRITS), mkv.parse_tags(TAGS_ECRITS))
        self.assertEqual(mkv.parse_tags("<Tags><Tag>"), set())
        self.assertEqual(mkv.parse_tags(None), set())

    def test_aller_retour_avec_ce_qu_on_ecrit(self):
        xml = mkv.tags_document([mkv.tag_block(50, [mkv.simple("TITLE", "Tom & Jerry")]),
                                 mkv.tag_block(70, [mkv.simple("PART_NUMBER", 2)])])
        self.assertEqual(mkv.parse_tags(xml),
                         {(50, "TITLE", "Tom & Jerry"), (70, "PART_NUMBER", "2")})

    def test_read_tags_passe_par_mkvextract(self):
        faux = TestOutilsExternes._faux_run(TAGS_ECRITS)
        with mock.patch.object(mkv.subprocess, "run", faux):
            tags = mkv.read_tags("film.mkv")
        self.assertEqual(len(tags), 3)


class TestVerificationDesTags(unittest.TestCase):
    def setUp(self):
        self.opts = mkv.Options(cover=False, date=False, audio_names=False, sub_names=False)
        self.target = mkv.Target(
            title="Iron Man",
            tags_xml=mkv.tags_document([mkv.tag_block(50, [mkv.simple("TITLE", "Iron Man"),
                                                           mkv.simple("GENRE", "Action")])]))
        self.info = {"container": {"properties": {"title": "Iron Man"}}, "tracks": []}

    def resultat(self, tags):
        return dict((lbl, (ok, det)) for lbl, ok, det
                    in mkv.verify(self.info, self.target, self.opts, tags))

    def test_tags_conformes(self):
        tags = mkv.parse_tags(self.target.tags_xml)
        self.assertTrue(mkv.is_conform(self.info, self.target, self.opts, tags))

    def test_tags_absents_signales(self):
        # Regression : un fichier vide de tags etait declare conforme, et --skip-done
        # le sautait.
        etat = self.resultat(set())
        self.assertFalse(etat["tags"][0])
        self.assertEqual(etat["tags"][1], "2 manquant(s), 0 en trop")

    def test_tags_perimes_signales(self):
        tags = {(50, "TITLE", "Iron Man"), (50, "GENRE", "Comedie")}
        self.assertEqual(self.resultat(tags)["tags"][1], "1 manquant(s), 1 en trop")

    def test_pas_de_controle_sans_relecture(self):
        self.assertNotIn("tags", self.resultat(None))
        self.assertTrue(mkv.is_conform(self.info, self.target, self.opts))


if __name__ == "__main__":
    unittest.main()
