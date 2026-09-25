"""Registre des essais évalués face au marché (plan, section 9).

Chaque variante évaluée sur la période de test avec cotes augmente la probabilité qu'une d'elles
paraisse rentable par pur hasard ; le nombre cumulé d'essais K sert à élargir les intervalles de
confiance (correction de Bonferroni). Relancer exactement le même essai (même date de référence,
même version du code, même cible, même variante) ne compte qu'une fois.
"""
from __future__ import annotations

import json
from pathlib import Path


class TrialRegistry:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def _key(rec: dict) -> tuple:
        return (rec["as_of"], rec["git_commit"], rec["cible"], rec["ligue"], rec["variante"])

    def k_for(self, rec: dict) -> int:
        """K que cet essai aura une fois enregistré (essais distincts pour cette cible et ligue)."""
        keys = {self._key(r) for r in self._records() if (r["cible"], r["ligue"]) == (rec["cible"], rec["ligue"])}
        keys.add(self._key(rec))
        return len(keys)

    def register(self, rec: dict) -> int:
        k = self.k_for(rec)
        if self._key(rec) not in {self._key(r) for r in self._records()}:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**rec, "k": k}, ensure_ascii=False, sort_keys=True) + "\n")
        return k
