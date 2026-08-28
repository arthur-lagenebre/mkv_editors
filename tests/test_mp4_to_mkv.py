"""Mp4_To_Mkv : quels fichiers un passage prend, et ce qu'il demande à mkvmerge.

Aucun outil externe n'est lancé : ce qui casse en silence ici, c'est la sélection des fichiers, la langue qu'on écrase alors qu'elle était juste, l'encodage supposé d'un sous-titre et l'ordre des options dans la commande - quatre choses qui se vérifient sans mkvmerge.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.Converters import Mp4_To_Mkv as conversion


def options(**overrides):
    """Les options d'un passage ordinaire, que chaque test tord à sa façon."""
    values = dict(lang="fre", force_lang=False, sub_lang="fre", sub_charset="windows-1252", subs="auto",
                  default_audio=False, default_sub=False, title=False,
                  apply=False, output_dir=None, overwrite=False)
    values.update(overrides)
    return types.SimpleNamespace(**values)


def audio(track_id, language=None):
    """Une piste audio telle que 'mkvmerge -J' la décrit, avec ou sans langue."""
    properties = {} if language is None else {"language": language}
    return {"id": track_id, "type": "audio", "properties": properties}


class TestSelectionDesFichiers(unittest.TestCase):
    """Une médiathèque mêle des films posés à plat et des dossiers de film."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "Blade (1998).mp4").write_bytes(b"")
        (self.root / "Deja converti.mkv").write_bytes(b"")
        (self.root / "Un vieux rip.avi").write_bytes(b"")     # celui-la est l'affaire d'Avi_To_Mkv
        folder = self.root / "Inception (2010)"
        folder.mkdir()
        (folder / "film.MP4").write_bytes(b"")                # les camescopes crient leur extension
        (folder / "notes.txt").write_text("x", encoding="utf-8")

    def test_recursif_par_defaut(self):
        # Les films posés à la racine d'abord, puis dossier par dossier.
        names = [p.name for p in conversion.mp4_files(self.root)]
        self.assertEqual(names, ["Blade (1998).mp4", "film.MP4"])

    def test_sans_recursion_l_etage_seul(self):
        names = [p.name for p in conversion.mp4_files(self.root, recursive=False)]
        self.assertEqual(names, ["Blade (1998).mp4"])


class TestLangueDesPistes(unittest.TestCase):
    """Un .mp4 déclare souvent ses langues : les écraser en perdrait de justes."""

    def languages(self, tracks, **overrides):
        return conversion.audio_languages({"tracks": tracks}, options(**overrides))

    def test_une_piste_muette_recoit_la_langue_demandee(self):
        to_write, kept = self.languages([audio(1, "und"), audio(2)])
        self.assertEqual(to_write, [(1, "fre"), (2, "fre")])     # "und" et champ absent, même chose
        self.assertEqual(kept, [])

    def test_une_langue_declaree_est_gardee(self):
        to_write, kept = self.languages([audio(1, "eng"), audio(2, "und")])
        self.assertEqual(to_write, [(2, "fre")])
        self.assertEqual(kept, ["eng"])

    def test_force_lang_reetiquette_tout(self):
        to_write, kept = self.languages([audio(1, "eng"), audio(2, "und")], force_lang=True)
        self.assertEqual(to_write, [(1, "fre"), (2, "fre")])
        self.assertEqual(kept, [])

    def test_la_video_ne_recoit_jamais_de_langue(self):
        tracks = [{"id": 0, "type": "video", "properties": {}}, audio(1)]
        to_write, _ = self.languages(tracks)
        self.assertEqual(to_write, [(1, "fre")])


class TestSousTitresAdjacents(unittest.TestCase):
    """Ce qu'on embarque avec le film, et ce qu'on laisse dehors."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.video = self.folder / "Le Film.mp4"
        for name in ("Le Film.mp4", "Le Film.fr.srt", "Le Film.en.srt", "Le Film.idx",
                     "Le Film.sub", "Le Film.txt", "Un Autre.fr.srt"):
            (self.folder / name).write_bytes(b"")

    def test_les_sous_titres_de_ce_film_seulement(self):
        names = [p.name for p in conversion.find_subtitles(self.video)]
        self.assertEqual(names, ["Le Film.en.srt", "Le Film.fr.srt", "Le Film.idx"])

    def test_le_sub_reste_a_son_idx(self):
        # Cité en plus du .idx, il ferait entrer la même piste deux fois.
        names = [p.name for p in conversion.find_subtitles(self.video)]
        self.assertNotIn("Le Film.sub", names)

    def test_langue_lue_dans_le_suffixe(self):
        language = lambda name: conversion.guess_sub_language(self.folder / name, self.video, "fre")
        self.assertEqual(language("Le Film.en.srt"), "eng")
        self.assertEqual(language("Le Film.vostfr.srt"), "fre")     # suffixe inconnu -> le défaut
        self.assertEqual(language("Le Film.srt"), "fre")            # rien à lire -> le défaut


class TestEncodageDesSousTitres(unittest.TestCase):
    """Un .srt mal deviné passe quand même, avec les accents en morceaux."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def write(self, name, raw):
        path = self.folder / name
        path.write_bytes(raw)
        return path

    def test_utf8_reconnu(self):
        path = self.write("a.srt", "Déjà vu".encode("utf-8"))
        self.assertEqual(conversion.detect_charset(path, "windows-1252"), "UTF-8")

    def test_latin1_retombe_sur_le_repli(self):
        path = self.write("b.srt", "Déjà vu".encode("cp1252"))
        self.assertEqual(conversion.detect_charset(path, "windows-1252"), "windows-1252")

    def test_les_bom_parlent_d_eux_memes(self):
        self.assertEqual(conversion.detect_charset(self.write("c.srt", b"\xef\xbb\xbfok"), "windows-1252"), "UTF-8")
        self.assertEqual(conversion.detect_charset(self.write("d.srt", b"\xff\xfeo\x00k\x00"), "windows-1252"), "UTF-16")


class TestCommandeMkvmerge(unittest.TestCase):
    """Une option mkvmerge s'applique au fichier qui la suit : l'ordre est le fond."""

    INFO = {"tracks": [{"id": 0, "type": "video", "properties": {}}, audio(1), audio(2)]}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.video = self.folder / "Le Film.mp4"
        self.video.write_bytes(b"")
        self.output = self.folder / "Le Film.mkv"
        self.fr = self.folder / "Le Film.fr.srt"
        self.fr.write_bytes("Déjà vu".encode("cp1252"))
        self.en = self.folder / "Le Film.en.srt"
        self.en.write_bytes("Already seen".encode("utf-8"))

    def build(self, subs=(), info=None, **overrides):
        return conversion.build_command(self.video, self.output, info or self.INFO,
                                        list(subs), options(**overrides))

    def test_chaque_piste_audio_muette_recoit_la_langue(self):
        command, _, _ = self.build(lang="eng")
        self.assertIn("1:eng", command)
        self.assertIn("2:eng", command)
        self.assertNotIn("0:eng", command)      # la piste 0 est la vidéo

    def test_une_piste_deja_etiquetee_n_est_pas_touchee(self):
        info = {"tracks": [{"id": 0, "type": "video", "properties": {}}, audio(1, "eng"), audio(2)]}
        command, _, kept = self.build(info=info)
        self.assertNotIn("1:fre", command)      # celle-ci se declare deja en anglais
        self.assertIn("2:fre", command)
        self.assertEqual(kept, ["eng"])

    def test_default_audio_ne_marque_que_la_premiere(self):
        command, _, _ = self.build(default_audio=True)
        self.assertEqual(command.count("--default-track-flag"), 1)
        self.assertIn("1:yes", command)

    def test_default_audio_marque_meme_une_piste_deja_etiquetee(self):
        # Le drapeau ne dépend pas de la langue : c'est la première piste audio qui le prend.
        info = {"tracks": [{"id": 0, "type": "video", "properties": {}}, audio(1, "eng")]}
        command, _, _ = self.build(info=info, default_audio=True)
        self.assertIn("1:yes", command)

    def test_les_options_precedent_leur_sous_titre(self):
        command, _, _ = self.build([self.fr])
        position = command.index(str(self.fr))
        self.assertEqual(command[position - 6:position],
                         ["--language", "0:fre", "--sub-charset", "0:windows-1252", "--default-track-flag", "0:no"])
        self.assertLess(command.index(str(self.video)), position)

    def test_un_encodage_mesure_par_fichier(self):
        _, attached, _ = self.build([self.en, self.fr])
        self.assertEqual(attached, [("Le Film.en.srt", "eng", "UTF-8"),
                                    ("Le Film.fr.srt", "fre", "windows-1252")])

    def test_default_sub_ne_marque_que_le_premier(self):
        command, _, _ = self.build([self.en, self.fr], default_sub=True)
        self.assertEqual(command.count("0:yes"), 1)
        self.assertLess(command.index("0:yes"), command.index(str(self.en)))

    def test_le_vobsub_n_a_pas_d_encodage(self):
        # Un .idx décrit des images : lui imposer un charset n'aurait aucun sens.
        idx = self.folder / "Le Film.idx"
        idx.write_bytes(b"")
        command, attached, _ = self.build([idx])
        self.assertNotIn("--sub-charset", command)
        self.assertEqual(attached, [("Le Film.idx", "fre", None)])

    def test_titre_pris_dans_le_nom_du_fichier(self):
        command, _, _ = self.build(title=True)
        self.assertEqual(command[command.index("--title") + 1], "Le Film")


class TestSousTitresDejaDansLeMp4(unittest.TestCase):
    """Contrairement à un .avi, un .mp4 peut déjà en porter : --subs require les compte."""

    VIDEO = {"id": 0, "type": "video", "properties": {}}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.video = Path(self._tmp.name) / "Le Film.mp4"
        self.video.write_bytes(b"")

    def convert(self, tracks, **overrides):
        info = {"tracks": [self.VIDEO, audio(1)] + tracks}
        with mock.patch.object(conversion, "identify", return_value=(info, "")):
            return conversion.convert(self.video, options(**overrides), check_duration=False)

    def test_require_accepte_un_sous_titre_embarque(self):
        status, _ = self.convert([{"id": 2, "type": "subtitles", "properties": {}}], subs="require")
        self.assertEqual(status, "simule")

    def test_require_ecarte_un_film_qui_n_en_a_nulle_part(self):
        status, detail = self.convert([], subs="require")
        self.assertEqual(status, "ignore")
        self.assertIn("--subs require", detail)

    def test_un_film_sans_piste_video_est_une_erreur(self):
        info = {"tracks": [audio(1)]}
        with mock.patch.object(conversion, "identify", return_value=(info, "")):
            status, detail = conversion.convert(self.video, options(), check_duration=False)
        self.assertEqual(status, "erreur")
        self.assertEqual(detail, "aucune piste video")


if __name__ == "__main__":
    unittest.main()
