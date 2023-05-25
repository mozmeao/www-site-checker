#!/bin/bash

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

if curl -s https://${HOSTNAME}/robots.txt | grep -q "Sitemap: https://www.mozilla.org/sitemap.xml";
then
    echo "https://${HOSTNAME}/robots.txt is OK"
else
    echo "CRITICAL: https://${HOSTNAME}/robots.txt is NOT OK"
    exit 99
fi
