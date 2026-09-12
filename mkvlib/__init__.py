"""mkvlib — briques communes aux scripts du dépôt (films et séries).

Les scripts de Movies/ et TV_Shows/ ne gardent que ce qui leur est propre : la logique TMDB, l'accès aux .mkv, l'analyse des noms de fichiers et les utilitaires de ligne de commande vivent ici, en un seul exemplaire.

La musique vit à part, dans musiclib/. Elle emprunte seulement à mkvlib ce qui ne dépend d'aucun domaine - cache, ligne de commande, bilan d'un passage, listage des fichiers : rien de ce qui s'y trouve ne doit donc supposer un film.

Aucune dépendance pip : uniquement la bibliothèque standard.
"""
