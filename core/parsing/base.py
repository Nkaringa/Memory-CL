from __future__ import annotations

from typing import Protocol

from schemas import IngestionUnit


class SourceParser(Protocol):
    """Anything that turns one source file into IngestionUnits.

    Implementations: PythonParser (stdlib ast), TreeSitterParser (JS/TS).
    """

    def parse_file(
        self,
        *,
        source: str,
        repo_id: str,
        file_path: str,
        commit_sha: str,
    ) -> list[IngestionUnit]: ...
