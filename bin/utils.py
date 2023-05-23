# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(SCRIPT_DIR))


def _get_configuration_path(pathname: str) -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = pathname.split("/")
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)


def _print(message: str) -> None:
    sys.stdout.write(message)
    sys.stdout.write("\n")


def _get_output_path() -> os.PathLike:
    # Get the path, allowing for this being called from the project root or the bin/ dir
    path_components = [
        "output",
    ]
    working_dir = os.getcwd()
    if str(working_dir).endswith("/bin"):
        path_components = [working_dir, ".."] + path_components
    return os.path.join("", *path_components)
