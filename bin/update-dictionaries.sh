#!/bin/bash

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

set -exo pipefail

# Fetch the latest en-US dictionary used in Firefox itself
# See https://searchfox.org/mozilla-central/source/extensions/spellcheck/locales/en-US/hunspell/

rm -rf "./data/base_dictionaries/en-US/"

wget "https://hg.mozilla.org/mozilla-central/raw-file/tip/extensions/spellcheck/locales/en-US/hunspell/en-US.aff" -P "./data/base_dictionaries/en-US/"
wget "https://hg.mozilla.org/mozilla-central/raw-file/tip/extensions/spellcheck/locales/en-US/hunspell/en-US.dic" -P "./data/base_dictionaries/en-US/"
wget "https://hg.mozilla.org/mozilla-central/raw-file/tip/extensions/spellcheck/locales/en-US/hunspell/README_en_US.txt" -P "./data/base_dictionaries/en-US/"
