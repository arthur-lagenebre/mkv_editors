"""mkvlib — briques communes aux scripts du depot (films et series).

Les scripts de Movies/ et TV_Shows/ ne gardent que ce qui leur est propre :
la logique TMDB, l'acces aux .mkv, l'analyse des noms de fichiers et les
utilitaires de ligne de commande vivent ici, en un seul exemplaire.

Aucune dependance pip : uniquement la bibliotheque standard.
"""
