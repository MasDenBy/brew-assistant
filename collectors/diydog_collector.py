"""BrewDog DIY Dog recipes -> BeerXML files.

Source: https://github.com/stuartraetaylor/diydog-beerxml
Automated export of BrewDog's DIY Dog recipe book into BeerXML 1.0:
~200 recipes as flat ``*.xml`` files at the repo root (~0.5 MB archive).

License note: the upstream repo declares **no license file**. The DIY Dog
book itself is published by BrewDog for free use (copy / brew / share).
Collected files are written to ``collect_dest`` (outside git) for personal
local use only -- nothing is committed or redistributed from this
repository. Verify terms with BrewDog before any redistribution.

Output contract (collectors/base.py): ``dest/recipes/*.xml``
"""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

from .base import DataCollector, SourceUnavailableError, register
from .web import fetch

ARCHIVE_URL = (
    "https://github.com/stuartraetaylor/diydog-beerxml/archive/refs/heads/master.zip"
)


def extract_beerxml(archive_bytes: bytes, dest: Path) -> list[Path]:
    """Extract every ``.xml`` member of the zip into ``dest/recipes/``.

    Idempotent: clears the collector's own subtree first, so stale files
    from previous runs never survive. Only the collector's namespace
    (``dest``) is touched. Raises SourceUnavailableError on a bad archive.
    """
    recipes_dir = dest / "recipes"
    if dest.exists():
        shutil.rmtree(dest)  # namespace rule: clear own subtree only
    recipes_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            written: list[Path] = []
            for member in zf.namelist():
                if not member.lower().endswith(".xml"):
                    continue
                target = recipes_dir / Path(member).name  # flat upstream layout
                target.write_bytes(zf.read(member))
                written.append(target)
    except zipfile.BadZipFile as exc:
        raise SourceUnavailableError(f"not a valid zip archive: {exc}") from exc
    return written


@register
class DiyDogCollector(DataCollector):
    id = "diydog"
    description = "BrewDog DIY Dog recipes (~200 BeerXML files)"

    def collect(self, dest: Path) -> list[Path]:
        # Download BEFORE clearing dest: a failed fetch must not wipe
        # previously collected recipes.
        archive = fetch(ARCHIVE_URL)
        written = extract_beerxml(archive, dest)
        if not written:
            raise SourceUnavailableError(
                f"no .xml recipes found in archive from {ARCHIVE_URL}"
            )
        return written
