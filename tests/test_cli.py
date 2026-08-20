"""Lecture du .env et resolution de la cle TMDB."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import cli


class TestDotenv(unittest.TestCase):
    def charger(self, contenu, environ=None):
        """Ecrit un .env dans un dossier temporaire et le charge depuis ce dossier."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".env").write_text(contenu, encoding="utf-8")
            with mock.patch.object(cli.Path, "cwd", staticmethod(lambda: Path(d))), \
                 mock.patch.dict(os.environ, environ or {}, clear=True), \
                 mock.patch.object(cli, "__file__", str(Path(d) / "cli.py")):
                cli.load_dotenv()
                return dict(os.environ)

    def test_paire_simple(self):
        self.assertEqual(self.charger("TMDB_KEY=abc123")["TMDB_KEY"], "abc123")

    def test_commentaires_et_lignes_vides_ignores(self):
        env = self.charger("# commentaire\n\nTMDB_KEY=abc\nligne sans egal\n")
        self.assertEqual(env["TMDB_KEY"], "abc")

    def test_guillemets_retires_et_prefixe_export(self):
        env = self.charger('export TMDB_KEY="abc"\nAUTRE=\'def\'\n')
        self.assertEqual((env["TMDB_KEY"], env["AUTRE"]), ("abc", "def"))

    def test_environnement_existant_prioritaire(self):
        # Une cle passee en variable d'environnement ne doit pas etre ecrasee par le .env.
        env = self.charger("TMDB_KEY=du_fichier", {"TMDB_KEY": "de_l_environnement"})
        self.assertEqual(env["TMDB_KEY"], "de_l_environnement")

    def test_bom_utf8_tolere(self):
        self.assertEqual(self.charger("﻿TMDB_KEY=abc")["TMDB_KEY"], "abc")


class TestCleTmdb(unittest.TestCase):
    def resoudre(self, environ, fallback=""):
        with mock.patch.dict(os.environ, environ, clear=True), \
             mock.patch.object(cli, "load_dotenv", lambda *a, **k: None):
            return cli.resolve_tmdb_key(fallback)

    def test_priorite_a_tmdb_api_key(self):
        self.assertEqual(self.resoudre({"TMDB_API_KEY": "a", "TMDB_KEY": "b"}, "c"), "a")

    def test_repli_sur_tmdb_key(self):
        self.assertEqual(self.resoudre({"TMDB_KEY": "b"}, "c"), "b")

    def test_repli_sur_la_constante_du_script(self):
        self.assertEqual(self.resoudre({}, "c"), "c")

    def test_aucune_cle_arrete_le_script(self):
        with self.assertRaises(SystemExit) as ctx:
            self.resoudre({})
        self.assertIn("Aucune cle TMDB", str(ctx.exception))


class TestBanniere(unittest.TestCase):
    def test_libelles(self):
        self.assertEqual(cli.mode_label(types.SimpleNamespace(apply=True, verify=False)),
                         "APPLICATION")
        self.assertEqual(cli.mode_label(types.SimpleNamespace(apply=True, verify=True)),
                         "VERIFICATION (aucune ecriture)")
        self.assertIn("SIMULATION", cli.mode_label(types.SimpleNamespace(apply=False, verify=False)))


if __name__ == "__main__":
    unittest.main()
