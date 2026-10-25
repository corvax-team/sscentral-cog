import asyncio
import logging
import re
from datetime import timezone

log = logging.getLogger("red.sscentral.dbfeed")

POLL_SECONDS = 10
PLAYER_MESSAGES = ("Ticket Opened", "Reply")
TAG_RE = re.compile(r"<[^>]+>")


class DbFeed:
    """Relays bans and ahelps from a game that only writes them to its own database"""

    def __init__(self, gamedb, server, database, on_ban, on_ticket_event):
        if not re.fullmatch(r"\w+", database):
            raise ValueError(f"bad database name {database!r}")
        self.gamedb = gamedb
        self.server = server
        self.database = database
        self.on_ban = on_ban
        self.on_ticket_event = on_ticket_event
        self.players = {}
        self.last_ban = 0
        self.last_ticket = 0

    async def run(self):
        self.last_ban = await self._max_id("ban")
        self.last_ticket = await self._max_id("ticket")
        while True:
            await asyncio.sleep(POLL_SECONDS)
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s feed failed", self.server)

    async def poll(self):
        bans = await self._rows_after("ban", self.last_ban)
        if bans:
            self.last_ban = bans[-1]["id"]
        for ban in _group_bans(bans):
            await self.on_ban({**ban, "server": self.server})

        for row in await self._rows_after("ticket", self.last_ticket):
            self.last_ticket = row["id"]
            event = self._ticket_event(row)
            if event:
                await self.on_ticket_event(event)

    def _ticket_event(self, row):
        key = (row["round_id"], row["ticket"])
        action = row["action"]
        sender = row["sender"]
        ticket_type = "Admin"
        if action == "Ticket Opened" and row["recipient"]:
            # an admin PM opened this ticket, so the recipient is the player
            self.players.setdefault(key, row["recipient"])
            action, ticket_type = "Reply", "Private Message"
        elif action == "Ticket Opened":
            action = "Reply" if key in self.players else "Opened"
            self.players.setdefault(key, sender)
        elif action in ("Disconnected", "Reconnected"):
            sender = row["recipient"]
        if key not in self.players:
            if action not in PLAYER_MESSAGES:
                return None
            self.players[key] = row["recipient"] or sender

        return {
            "server": self.server,
            "round_id": row["round_id"],
            "ticket_id": row["ticket"],
            "player_ckey": self.players[key],
            "ticket_type": ticket_type,
            "action": action,
            "sender": sender,
            "message": TAG_RE.sub("", row["message"] or ""),
            "admins_online": None,
            "urgent": bool(row["urgent"]),
        }

    async def _max_id(self, table):
        rows = await self.gamedb._rows(f"SELECT COALESCE(MAX(id), 0) AS id FROM `{self.database}`.`{table}`", ())
        return rows[0]["id"]

    async def _rows_after(self, table, last_id):
        return await self.gamedb._rows(
            f"SELECT * FROM `{self.database}`.`{table}` WHERE id > %s ORDER BY id LIMIT 200", (last_id,)
        )


def _group_bans(rows):
    # the game writes one row per banned role
    groups = {}
    for row in rows:
        key = (row["bantime"], row["ckey"], row["a_ckey"], row["reason"])
        groups.setdefault(key, []).append(row)
    return [_ban_entry(group) for group in groups.values()]


def _ban_entry(rows):
    first = rows[0]
    roles = [r["role"] for r in rows if r["role"] and r["role"] != "Server"]
    issued = first["bantime"].replace(tzinfo=timezone.utc)
    expires = first["expiration_time"].replace(tzinfo=timezone.utc) if first["expiration_time"] else None
    return {
        "id": first["id"],
        "player_ckey": first["ckey"] or "без ckey",
        "admin_ckey": first["a_ckey"],
        "reason": first["reason"],
        "job": ",".join(roles) or None,
        "duration_hours": (expires - issued).total_seconds() / 3600 if expires else None,
        "issue_time": issued.isoformat(),
        "expiration_time": expires.isoformat() if expires else None,
        "round_id": first["round_id"],
    }
