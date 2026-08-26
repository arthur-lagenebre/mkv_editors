"""Lecture du .env et résolution de la clé TMDB."""

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
    def charger(self, contenu):
        """Écrit un .env dans un dossier temporaire et le lit depuis ce dossier."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".env").write_text(contenu, encoding="utf-8")
            with mock.patch.object(cli.Path, "cwd", staticmethod(lambda: Path(d))), mock.patch.object(cli, "__file__", str(Path(d) / "cli.py")):
                return cli.read_dotenv()

    def test_paire_simple(self):
        self.assertEqual(self.charger("TMDB_KEY=abc123"), {"TMDB_KEY": "abc123"})

    def test_commentaires_et_lignes_vides_ignores(self):
        env = self.charger("# commentaire\n\nTMDB_KEY=abc\nligne sans egal\n")
        self.assertEqual(env, {"TMDB_KEY": "abc"})

    def test_guillemets_retires_et_prefixe_export(self):
        env = self.charger("export TMDB_KEY=\"abc\"\nAUTRE='def'\n")
        self.assertEqual((env["TMDB_KEY"], env["AUTRE"]), ("abc", "def"))

    def test_premiere_ligne_gagne(self):
        self.assertEqual(self.charger("TMDB_KEY=un\nTMDB_KEY=deux")["TMDB_KEY"], "un")

    def test_bom_utf8_tolere(self):
        self.assertEqual(self.charger("﻿TMDB_KEY=abc")["TMDB_KEY"], "abc")

    def test_fichier_absent(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(cli.Path, "cwd", staticmethod(lambda: Path(d))), mock.patch.object(cli, "__file__", str(Path(d) / "cli.py")):
                self.assertEqual(cli.read_dotenv("nexiste_pas.env"), {})

    def test_environnement_ni_lu_ni_ecrit(self):
        # Le .env est la seule source : une variable d'environnement ne le remplace plus, et la lecture ne laisse rien derrière elle.
        with mock.patch.dict(os.environ, {"TMDB_KEY": "de_l_environnement"}, clear=True):
            self.assertEqual(self.charger("TMDB_KEY=du_fichier")["TMDB_KEY"], "du_fichier")
            self.assertEqual(os.environ["TMDB_KEY"], "de_l_environnement")


class TestCleTmdb(unittest.TestCase):
    def resoudre(self, valeurs):
        with mock.patch.object(cli, "read_dotenv", lambda *a, **k: valeurs):
            return cli.resolve_tmdb_key()

    def test_cle_du_fichier(self):
        self.assertEqual(self.resoudre({"TMDB_KEY": "abc123"}), "abc123")

    def test_espaces_ignores(self):
        self.assertEqual(self.resoudre({"TMDB_KEY": "  abc123  "}), "abc123")

    def test_fichier_sans_cle(self):
        with self.assertRaises(SystemExit) as ctx:
            self.resoudre({"AUTRE": "x"})
        self.assertIn("TMDB_KEY", str(ctx.exception))

    def test_cle_vide(self):
        with self.assertRaises(SystemExit):
            self.resoudre({"TMDB_KEY": ""})

    def test_aucun_fichier(self):
        with self.assertRaises(SystemExit) as ctx:
            self.resoudre({})
        self.assertIn(".env", str(ctx.exception))


class TestBanniere(unittest.TestCase):
    def test_libelles(self):
        self.assertEqual(cli.mode_label(types.SimpleNamespace(apply=True, verify=False)), "APPLICATION")
        self.assertEqual(cli.mode_label(types.SimpleNamespace(apply=True, verify=True)), "VERIFICATION (aucune ecriture)")
        self.assertIn("SIMULATION", cli.mode_label(types.SimpleNamespace(apply=False, verify=False)))

class TestDossier(unittest.TestCase):
    def test_dossier_valide_rendu(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cli.check_dir(d), Path(d))

    def test_dossier_introuvable(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.check_dir("dossier_qui_n_existe_pas")
        self.assertIn("introuvable", str(ctx.exception))

    def test_fichier_au_lieu_d_un_dossier(self):
        with tempfile.TemporaryDirectory() as d:
            fichier = Path(d) / "film.mkv"
            fichier.write_text("x", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                cli.check_dir(fichier)
        self.assertIn("pas un fichier", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
