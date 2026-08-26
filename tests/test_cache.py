"""Cache disque des réponses TMDB."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mkvlib import cache


class CacheTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dossier = Path(self._tmp.name) / "tmdb"
        self.addCleanup(self._tmp.cleanup)

    def cache(self, **kwargs):
        return cache.Cache(self.dossier, **kwargs)

    def vieillir(self, secondes):
        """Recule la date de toutes les entrées, pour simuler le temps qui passe."""
        for f in self.dossier.glob("*.json"):
            ancien = time.time() - secondes
            os.utime(f, (ancien, ancien))


class TestCache(CacheTestCase):
    def test_aller_retour(self):
        c = self.cache()
        c.put("fr-FR:movie/1", {"title": "Dune"})
        self.assertEqual(c.get("fr-FR:movie/1"), {"title": "Dune"})

    def test_entree_absente(self):
        self.assertIsNone(self.cache().get("fr-FR:movie/1"))

    def test_entree_perimee(self):
        c = self.cache(ttl=100)
        c.put("k", {"a": 1})
        self.vieillir(200)
        self.assertIsNone(c.get("k"))

    def test_cles_distinctes_par_langue(self):
        c = self.cache()
        c.put("fr-FR:movie/1", {"t": "Le Loup"})
        c.put("en-US:movie/1", {"t": "The Wolf"})
        self.assertEqual(c.get("en-US:movie/1"), {"t": "The Wolf"})

    def test_fichier_corrompu_vaut_absence(self):
        c = self.cache()
        c.put("k", {"a": 1})
        for f in self.dossier.glob("*.json"):
            f.write_text("ceci n'est pas du json", encoding="utf-8")
        self.assertIsNone(c.get("k"))

    def test_cle_differente_dans_le_fichier(self):
        # Garde-fou : le contenu porte sa clé, une collision d'empreinte ne sert rien.
        c = self.cache()
        c.put("k", {"a": 1})
        for f in self.dossier.glob("*.json"):
            f.write_text(json.dumps({"key": "autre", "body": {"a": 2}}), encoding="utf-8")
        self.assertIsNone(c.get("k"))

    def test_no_cache_ignore_la_lecture_mais_rafraichit(self):
        chaud = self.cache()
        chaud.put("k", {"a": 1})
        froid = self.cache(read=False)
        self.assertIsNone(froid.get("k"))          # on ne lit plus
        froid.put("k", {"a": 2})
        self.assertEqual(chaud.get("k"), {"a": 2})  # mais on a bien rafraîchi

    def test_purge_des_entrees_perimees(self):
        c = self.cache(ttl=100)
        c.put("vieux", {"a": 1})
        self.vieillir(200)
        c._purged = False                          # une purge par exécution
        c.put("neuf", {"a": 2})
        self.assertEqual(len(list(self.dossier.glob("*.json"))), 1)

    def test_ecriture_impossible_sans_consequence(self):
        # Un fichier à la place du dossier parent : la création échouera, sur n'importe quel système. ("Z:/inexistant" n'était absolu que sous Windows - ailleurs, c'était un chemin relatif que le test créait sans problème.)
        bloqueur = Path(self._tmp.name) / "bloqueur"
        bloqueur.write_text("je ne suis pas un dossier", encoding="utf-8")
        c = cache.Cache(bloqueur / "tmdb")
        c.put("k", {"a": 1})                       # ne doit rien lever
        self.assertIsNone(c.get("k"))

    def test_valeur_non_serialisable_ignoree(self):
        c = self.cache()
        c.put("k", {"a": {1, 2}})                  # un set ne passe pas en JSON
        self.assertIsNone(c.get("k"))


class TestEmplacement(unittest.TestCase):
    def test_hors_de_la_mediatheque(self):
        # Rien ne doit apparaitre à côté des films.
        dossier = cache.default_folder()
        self.assertEqual(dossier.name, "tmdb")
        self.assertEqual(dossier.parent.name, "mkv_editors")


if __name__ == "__main__":
    unittest.main()
