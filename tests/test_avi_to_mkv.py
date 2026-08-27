"""Avi_To_Mkv : quels fichiers un passage prend, et ce qu'il demande à mkvmerge.

Aucun outil externe n'est lancé : ce qui casse en silence ici, c'est la sélection des fichiers, l'encodage supposé d'un sous-titre et l'ordre des options dans la commande - trois choses qui se vérifient sans mkvmerge.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.Converters import Avi_To_Mkv as conversion


def options(**overrides):
    """Les options d'un passage ordinaire, que chaque test tord à sa façon."""
    values = dict(lang="fre", sub_lang="fre", sub_charset="windows-1252", subs="auto",
                  default_audio=False, default_sub=False, title=False)
    values.update(overrides)
    return types.SimpleNamespace(**values)


class TestSelectionDesFichiers(unittest.TestCase):
    """Une médiathèque mêle des films posés à plat et des dossiers de film."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "Blade (1998).avi").write_bytes(b"")
        (self.root / "Deja converti.mkv").write_bytes(b"")
        folder = self.root / "Inception (2010)"
        folder.mkdir()
        (folder / "film.AVI").write_bytes(b"")       # les rips d'époque crient leur extension
        (folder / "notes.txt").write_text("x", encoding="utf-8")

    def test_recursif_par_defaut(self):
        # Les films posés à la racine d'abord, puis dossier par dossier.
        names = [p.name for p in conversion.avi_files(self.root)]
        self.assertEqual(names, ["Blade (1998).avi", "film.AVI"])

    def test_sans_recursion_l_etage_seul(self):
        names = [p.name for p in conversion.avi_files(self.root, recursive=False)]
        self.assertEqual(names, ["Blade (1998).avi"])


class TestSousTitresAdjacents(unittest.TestCase):
    """Ce qu'on embarque avec le film, et ce qu'on laisse dehors."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.video = self.folder / "Le Film.avi"
        for name in ("Le Film.avi", "Le Film.fr.srt", "Le Film.en.srt", "Le Film.idx",
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

    def test_un_texte_sans_accent_est_de_l_utf8(self):
        # L'ASCII est de l'UTF-8 valide : rien à convertir, donc rien à casser.
        path = self.write("e.srt", b"plain text")
        self.assertEqual(conversion.detect_charset(path, "windows-1252"), "UTF-8")


class TestCommandeMkvmerge(unittest.TestCase):
    """Une option mkvmerge s'applique au fichier qui la suit : l'ordre est le fond."""

    INFO = {"tracks": [{"id": 0, "type": "video"}, {"id": 1, "type": "audio"}, {"id": 2, "type": "audio"}]}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)
        self.video = self.folder / "Le Film.avi"
        self.video.write_bytes(b"")
        self.output = self.folder / "Le Film.mkv"
        self.fr = self.folder / "Le Film.fr.srt"
        self.fr.write_bytes("Déjà vu".encode("cp1252"))
        self.en = self.folder / "Le Film.en.srt"
        self.en.write_bytes("Already seen".encode("utf-8"))

    def build(self, subs=(), **overrides):
        return conversion.build_command(self.video, self.output, self.INFO, list(subs), options(**overrides))

    def test_chaque_piste_audio_recoit_la_langue(self):
        command, _ = self.build(lang="eng")
        self.assertIn("1:eng", command)
        self.assertIn("2:eng", command)
        self.assertNotIn("0:eng", command)      # la piste 0 est la vidéo

    def test_default_audio_ne_marque_que_la_premiere(self):
        command, _ = self.build(default_audio=True)
        self.assertEqual(command.count("--default-track-flag"), 1)
        self.assertIn("1:yes", command)

    def test_les_options_precedent_leur_sous_titre(self):
        command, _ = self.build([self.fr])
        position = command.index(str(self.fr))
        self.assertEqual(command[position - 6:position],
                         ["--language", "0:fre", "--sub-charset", "0:windows-1252", "--default-track-flag", "0:no"])
        self.assertLess(command.index(str(self.video)), position)

    def test_un_encodage_mesure_par_fichier(self):
        _, attached = self.build([self.en, self.fr])
        self.assertEqual(attached, [("Le Film.en.srt", "eng", "UTF-8"),
                                    ("Le Film.fr.srt", "fre", "windows-1252")])

    def test_default_sub_ne_marque_que_le_premier(self):
        command, _ = self.build([self.en, self.fr], default_sub=True)
        self.assertEqual(command.count("0:yes"), 1)
        self.assertLess(command.index("0:yes"), command.index(str(self.en)))

    def test_le_vobsub_n_a_pas_d_encodage(self):
        # Un .idx décrit des images : lui imposer un charset n'aurait aucun sens.
        idx = self.folder / "Le Film.idx"
        idx.write_bytes(b"")
        command, attached = self.build([idx])
        self.assertNotIn("--sub-charset", command)
        self.assertEqual(attached, [("Le Film.idx", "fre", None)])

    def test_titre_pris_dans_le_nom_du_fichier(self):
        command, _ = self.build(title=True)
        self.assertEqual(command[command.index("--title") + 1], "Le Film")


if __name__ == "__main__":
    unittest.main()
