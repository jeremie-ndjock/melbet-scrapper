"""Résultats officiels : récupération, analyse du score, et rattrapage de l'historique.

Règles mesurées (Memoire.md, section 2.2) : ``champId`` au singulier, ``dateFrom``/``dateTo``
alignés sur des multiples de 300 s, une fenêtre d'au plus 2 jours par requête. L'historique remonte
à au moins 90 jours.

Format du score, ex. Mortal Kombat X : ``5:0(1:0 F;1:0 R;1:0 R;1:0 R;1:0 F)``.
Mortal Kombat 3 ajoute un suffixe Mercy à chaque round : ``5:3(0:1 F ,M- / M-; 1:0 Ba ,M- / M-; ...)``.
Codes de finish observés : R, F, B, Ba, Fr, An, Hk (une ou deux lettres).

Autre format rencontré, AI Table Tennis (Memoire.md, section 27) : ``2:0 (11:7,11:7)`` — score final
en sets, puis score de chaque set entre parenthèses, séparés par des virgules (pas de type de
finish, pas de suffixe Mercy — plus simple à analyser). Voir ``parse_table_tennis_score``.

Le sport (``sport_id``) doit être passé explicitement à chaque appel plutôt que supposé : un vrai
défaut a existé ici (``sportIds=103`` codé en dur, jamais paramétré tant qu'un seul sport était
collecté) — corrigé avant qu'il ne cause un rattrapage silencieusement vide pour un autre sport.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, ValidationError

from .storage import queries
from .transport.errors import ParserError
from .transport.http import HttpClient

log = logging.getLogger("collector.results")

ENDPOINT = "/service-api/result/web/api/v3/games"
WINDOW = timedelta(days=2)
_ALIGN_SECONDS = 300

_SCORE_RE = re.compile(r"^(\d+):(\d+)\((.*)\)$")
_ROUND_RE = re.compile(r"(\d+):(\d+)\s+([A-Za-z]{1,3})(?:\s*,\s*(M[+-])\s*/\s*(M[+-]))?")


class ResultParseError(Exception):
    """Le champ ``score`` ne correspond à aucun format connu."""


class RawResultGame(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int
    champId: int
    opp1: str
    opp2: str
    opp1Ids: list[int] = []
    opp2Ids: list[int] = []
    score: str
    dateStart: int


class ResultsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    count: int = 0
    items: list[RawResultGame] = []


@dataclass(frozen=True)
class ParsedRound:
    round_no: int
    winner: int
    finish_code: str
    mercy_p1: bool | None
    mercy_p2: bool | None


@dataclass(frozen=True)
class ParsedScore:
    final1: int
    final2: int
    winner: int | None  # None : match terminé à égalité (observé en réel, voir migrations/005)
    rounds: list[ParsedRound]


def parse_score(raw: str) -> ParsedScore:
    """Analyse la chaîne de score. Un round au format inattendu est ignoré (avec un avertissement),
    pas fatal ; seule une chaîne globalement méconnaissable lève ``ResultParseError``.

    Un score final à égalité est accepté (``winner=None``) : observé en réel le 2026-09-21 (un
    match Mortal Kombat 3 terminé 2:2), vraisemblablement une interruption ou un incident
    technique côté site plutôt qu'une règle du jeu, mais bien réel — l'ignorer perdrait ce résultat.
    """
    m = _SCORE_RE.match(raw.strip())
    if not m:
        raise ResultParseError(f"format de score méconnaissable : {raw!r}")
    final1, final2 = int(m.group(1)), int(m.group(2))
    winner = 1 if final1 > final2 else (2 if final2 > final1 else None)

    rounds: list[ParsedRound] = []
    for i, fragment in enumerate(m.group(3).split(";"), start=1):
        rm = _ROUND_RE.search(fragment)
        if not rm:
            log.warning("round %d illisible dans le score %r, ignoré", i, raw)
            continue
        s1, s2, finish_code, mercy1, mercy2 = rm.groups()
        rounds.append(ParsedRound(
            round_no=i,
            winner=1 if int(s1) > int(s2) else 2,
            finish_code=finish_code,
            mercy_p1=None if mercy1 is None else mercy1 == "M+",
            mercy_p2=None if mercy2 is None else mercy2 == "M+",
        ))
    return ParsedScore(final1, final2, winner=winner, rounds=rounds)


_TT_SCORE_RE = re.compile(r"^(\d+):(\d+)\s*\((.*)\)$")
_TT_SET_RE = re.compile(r"^(\d+):(\d+)$")


@dataclass(frozen=True)
class ParsedSet:
    set_no: int
    points1: int
    points2: int
    winner: int


@dataclass(frozen=True)
class TableTennisScore:
    """Pendant de ``ParsedScore`` pour AI Table Tennis : pas de type de finish ni de Mercy, mais
    le score point par point de chaque set (``sets``), une granularité que Mortal Kombat n'a pas
    (Memoire.md, section 27). ``final1``/``final2``/``winner`` ont le même sens que pour
    ``ParsedScore`` — les deux formes sont interchangeables pour ``store_results``."""
    final1: int
    final2: int
    winner: int | None
    sets: list[ParsedSet]


def parse_table_tennis_score(raw: str) -> TableTennisScore:
    """Analyse ``"2:0 (11:7,11:7)"``. Un set au format inattendu est ignoré (avec un
    avertissement), pas fatal — même politique de tolérance que ``parse_score``."""
    m = _TT_SCORE_RE.match(raw.strip())
    if not m:
        raise ResultParseError(f"format de score méconnaissable : {raw!r}")
    final1, final2 = int(m.group(1)), int(m.group(2))
    winner = 1 if final1 > final2 else (2 if final2 > final1 else None)

    sets: list[ParsedSet] = []
    for i, fragment in enumerate(m.group(3).split(","), start=1):
        sm = _TT_SET_RE.match(fragment.strip())
        if not sm:
            log.warning("set %d illisible dans le score %r, ignoré", i, raw)
            continue
        p1, p2 = int(sm.group(1)), int(sm.group(2))
        if p1 == p2:
            log.warning("set %d à égalité (%r) dans le score %r, ignoré", i, fragment, raw)
            continue
        sets.append(ParsedSet(set_no=i, points1=p1, points2=p2, winner=1 if p1 > p2 else 2))
    return TableTennisScore(final1, final2, winner=winner, sets=sets)


def align_down(ts: datetime) -> datetime:
    """Arrondit un horodatage au multiple de 300 s inférieur (exigence du service de résultats)."""
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % _ALIGN_SECONDS, tz=timezone.utc)


def iter_windows(*, end: datetime, days_back: int) -> list[tuple[datetime, datetime]]:
    """Fenêtres de 2 jours consécutives, alignées sur 300 s, couvrant [end - days_back, end]."""
    end = align_down(end) + timedelta(seconds=_ALIGN_SECONDS)  # borne haute exclusive alignée
    start_limit = end - timedelta(days=days_back)
    windows = []
    cursor = end
    while cursor > start_limit:
        window_start = max(cursor - WINDOW, start_limit)
        windows.append((window_start, cursor))
        cursor = window_start
    return windows


async def fetch_results(client: HttpClient, champ_id: int, date_from: datetime, date_to: datetime,
                         site_params: dict[str, object], *, sport_id: int) -> list[RawResultGame]:
    """``sport_id`` est obligatoire, jamais supposé : un défaut réel a existé ici (103 codé en dur,
    jamais remarqué tant qu'un seul sport était collecté) — voir l'en-tête du module."""
    params = {"champId": champ_id, "dateFrom": int(date_from.timestamp()), "dateTo": int(date_to.timestamp()),
              "sportIds": sport_id, **site_params}
    result = await client.get(ENDPOINT, params)
    try:
        data = json.loads(result.body)
        return ResultsResponse.model_validate(data).items
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ParserError(f"réponse de résultats invalide : {exc}", endpoint=ENDPOINT, payload=result.body) from exc


async def store_results(conn, league_id: int, games: list[RawResultGame], *,
                         parse_fn=parse_score) -> int:
    """Enregistre les résultats et, si le format analysé les fournit (Mortal Kombat seulement à ce
    jour — voir ``ParsedScore.rounds`` vs ``TableTennisScore.sets``), le détail des rounds.
    Idempotent : un résultat déjà connu n'est pas remplacé ; un round déjà décrit par le tableau
    des rounds en direct n'est que complété (code de finish, Mercy), jamais écrasé (voir
    migrations/002 et storage/queries.py).

    ``parse_fn`` rend cette fonction réutilisable pour n'importe quel format de score (passer
    ``parse_table_tennis_score`` pour AI Table Tennis) sans dupliquer la boucle d'écriture ni la
    logique de rattrapage (``backfill_results``/``reconcile_recent``, inchangées).

    Écritures groupées (``executemany``) : une fenêtre de 2 jours peut contenir plusieurs centaines
    de matchs (mesuré : jusqu'à environ 289 par jour et par ligue, Memoire.md section 2.2). Écrire
    chaque résultat et chaque round un par un multiplierait les allers-retours vers la base par
    plusieurs centaines pour une seule fenêtre de rattrapage.
    """
    result_rows = []
    round_rows = []
    for game in games:
        try:
            parsed = parse_fn(game.score)
        except ResultParseError as exc:
            log.error("résultat %s ignoré : %s", game.id, exc)
            continue
        result_rows.append((
            game.id, league_id, game.opp1Ids[0] if game.opp1Ids else None,
            game.opp2Ids[0] if game.opp2Ids else None, game.opp1, game.opp2,
            parsed.final1, parsed.final2, parsed.winner, game.score,
            datetime.fromtimestamp(game.dateStart, tz=timezone.utc), 3,
        ))
        rounds = getattr(parsed, "rounds", None)  # absent pour TableTennisScore : pas de round_results
        if rounds:
            round_rows.extend(
                (game.id, r.round_no, r.winner, None, None, r.finish_code, None, None, r.mercy_p1, r.mercy_p2)
                for r in rounds
            )
    if result_rows:
        await conn.executemany(queries.INSERT_RESULT, result_rows)
    if round_rows:
        await conn.executemany(queries.UPSERT_ROUND_RESULT, round_rows)
    return len(result_rows)


async def _get_backfill_state(conn, league_id: int) -> tuple[datetime, datetime] | tuple[None, None]:
    row = await conn.fetchrow(queries.SELECT_CHECKPOINT, "results", f"backfill_{league_id}")
    if row is None:
        return None, None
    value = json.loads(row["value"])
    return datetime.fromisoformat(value["anchor"]), datetime.fromisoformat(value["oldest_covered"])


async def _set_backfill_state(conn, league_id: int, anchor: datetime, oldest_covered: datetime) -> None:
    await conn.execute(queries.UPSERT_CHECKPOINT, "results", f"backfill_{league_id}",
                        json.dumps({"anchor": anchor.isoformat(), "oldest_covered": oldest_covered.isoformat()}))


async def backfill_results(client: HttpClient, conn, league_id: int, site_params: dict[str, object],
                            *, sport_id: int, days_back: int, now: datetime | None = None,
                            should_stop=lambda: False, parse_fn=parse_score) -> int:
    """Remonte l'historique des résultats fenêtre par fenêtre, en reprenant après une interruption
    grâce à un point de contrôle.

    Important : la borne haute de la plage (l'« ancre ») est fixée une seule fois, à la toute
    première exécution, et **jamais recalculée** à partir de l'horloge courante lors d'une reprise.
    Sans cela, chaque reprise recalculerait sa propre grille de fenêtres à partir de « maintenant »,
    qui aurait avancé entre-temps : la grille se décalerait d'une exécution à l'autre et l'intervalle
    entre l'ancienne et la nouvelle « maintenant » ne serait jamais couvert. ``now`` ne sert donc
    qu'à fixer l'ancre lors du tout premier appel ; les reprises l'ignorent et réutilisent l'ancre
    déjà enregistrée. Cette fonction couvre l'historique profond (jusqu'à ``days_back`` jours dans
    le passé) ; les résultats plus récents que l'ancre sont à la charge de ``reconcile_recent``.

    ``days_back`` doit rester **constant** d'un appel à l'autre pour une même ligue (c'est le cas
    en usage normal : sa valeur vient de la configuration, jamais changée entre deux démarrages).
    Le changer en cours de route peut décaler la dernière fenêtre (celle qui touche exactement
    ``anchor - days_back``) et laisser un court intervalle non couvert près de cette limite. Pour
    revoir la profondeur de rattrapage d'une ligue, effacer son point de contrôle avant de relancer.
    """
    anchor, already_covered = await _get_backfill_state(conn, league_id)
    anchor = anchor or now or datetime.now(timezone.utc)

    windows = iter_windows(end=anchor, days_back=days_back)
    if already_covered is not None:
        # Une fenêtre est déjà traitée si sa borne haute est dans la zone déjà couverte
        # (de `already_covered` à l'ancre) : ne garder que ce qui est plus ancien.
        windows = [(a, b) for a, b in windows if b <= already_covered]

    n_total = 0
    for window_start, window_end in windows:
        if should_stop():
            break  # arrêt demandé (redémarrage, blocage détecté ailleurs) : reprendra à ce point
        games = await fetch_results(client, league_id, window_start, window_end, site_params, sport_id=sport_id)
        n_total += await store_results(conn, league_id, games, parse_fn=parse_fn)
        await _set_backfill_state(conn, league_id, anchor, window_start)
    return n_total


async def reconcile_recent(client: HttpClient, conn, league_id: int, site_params: dict[str, object],
                            *, sport_id: int, lookback_hours: int = 2, now: datetime | None = None,
                            parse_fn=parse_score) -> int:
    """Récupère les résultats des dernières heures (par défaut 2 h, largement supérieur à la durée
    d'un match), pour capter les matchs terminés depuis le dernier passage.

    Volontairement sans point de contrôle : ``store_results`` est idempotent, donc rejouer la même
    fenêtre récente à chaque appel (toutes les 5 min, voir docs/architecture.md) ne coûte qu'une
    requête légère et ne crée jamais de doublon.
    """
    now = now or datetime.now(timezone.utc)
    window_end = align_down(now) + timedelta(seconds=_ALIGN_SECONDS)
    window_start = align_down(now - timedelta(hours=lookback_hours))
    games = await fetch_results(client, league_id, window_start, window_end, site_params, sport_id=sport_id)
    return await store_results(conn, league_id, games, parse_fn=parse_fn)
