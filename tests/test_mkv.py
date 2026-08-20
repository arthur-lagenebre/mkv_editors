"""Noms de pistes, tags XML et comparaison a l'etat vise (aucun outil externe requis)."""

import sys
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
