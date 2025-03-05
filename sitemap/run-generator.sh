#!/bin/bash
set -ex
bin/run-db-download.py
python manage.py l10n_update
python manage.py update_sitemaps
cp /app/root_files/sitemap.json /app/sitemap-data/
