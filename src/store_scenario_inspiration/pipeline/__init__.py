"""The store pipeline, as importable stages rather than command line scripts.

Each stage reads the JSON files the stages before it wrote, does its work, and
writes one more. ``scripts/`` drives these same functions for batch runs, so the
command line and the web app cannot drift apart.
"""
