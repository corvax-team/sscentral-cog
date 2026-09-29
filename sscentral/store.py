import re
from datetime import datetime, timedelta, timezone

from redbot.core import Config

CONFIG_ID = 0x55C3A7A1


class LinkConflict(Exception):
    pass


class Store:
    def __init__(self, cog):
        self.config = Config.get_conf(cog, identifier=CONFIG_ID, force_registration=True)
        self.config.register_global(
            players={}, whitelist=[], whitelist_bans=[], donates=[], bans=[], next_id=1
        )

    async def player_by_ckey(self, ckey):
        players = await self.config.players()
        return players.get(ckey)

    async def player_by_discord(self, discord_id):
        players = await self.config.players()
        for player in players.values():
            if player["discord_id"] == str(discord_id):
                return player
        return None

    async def link(self, ckey, discord_id):
        discord_id = str(discord_id)
        async with self.config.players() as players:
            if ckey in players:
                raise LinkConflict("ckey")
            if any(p["discord_id"] == discord_id for p in players.values()):
                raise LinkConflict("discord")
            players[ckey] = {"ckey": ckey, "discord_id": discord_id, "linked_at": _iso(_now())}
            return players[ckey]

    async def unlink(self, ckey):
        async with self.config.players() as players:
            return players.pop(ckey, None)

    async def whitelist(self, server_type=None, ckey=None, active_only=True):
        entries = await self.config.whitelist()
        return _select(entries, server_type, ckey, active_only)

    async def add_whitelist(self, ckey, admin_ckey, server_type, duration_days):
        entry = {
            "player_ckey": ckey,
            "admin_ckey": admin_ckey,
            "server_type": server_type,
            **_validity(duration_days, 24),
        }
        return await self._append(self.config.whitelist, entry)

    async def revoke_whitelist(self, ckey, server_type):
        async with self.config.whitelist() as entries:
            revoked = _select(entries, server_type, ckey, True)
            for entry in revoked:
                entry["valid"] = False
            return len(revoked)

    async def whitelist_bans(self, server_type=None, ckey=None, active_only=True):
        entries = await self.config.whitelist_bans()
        return _select(entries, server_type, ckey, active_only)

    async def add_whitelist_ban(self, ckey, admin_ckey, server_type, duration_days, reason):
        entry = {
            "player_ckey": ckey,
            "admin_ckey": admin_ckey,
            "server_type": server_type,
            "reason": reason,
            **_validity(duration_days, 24),
        }
        await self.revoke_whitelist(ckey, server_type)
        return await self._append(self.config.whitelist_bans, entry)

    async def donates(self, ckey=None, active_only=True):
        entries = await self.config.donates()
        return _select(entries, None, ckey, active_only)

    async def set_tier(self, ckey, tier, duration_days):
        await self.clear_tier(ckey)
        entry = {"player_ckey": ckey, "tier": tier, **_validity(duration_days, 24)}
        return await self._append(self.config.donates, entry)

    async def clear_tier(self, ckey):
        async with self.config.donates() as entries:
            active = _select(entries, None, ckey, True)
            for entry in active:
                entry["valid"] = False
            return len(active)

    async def bans(self, ckey=None):
        entries = await self.config.bans()
        return [e for e in entries if ckey is None or e["player_ckey"] == ckey]

    async def add_ban(self, record):
        duration_hours = record.get("duration_hours")
        entry = {**record, **_validity(duration_hours, 1)}
        return await self._append(self.config.bans, entry)

    async def _append(self, group, entry):
        async with self.config.next_id.get_lock():
            entry["id"] = await self.config.next_id()
            await self.config.next_id.set(entry["id"] + 1)
        async with group() as entries:
            entries.append(entry)
        return entry


def canonical_ckey(raw):
    return re.sub(r"[^a-z0-9@]", "", str(raw).lower())


def is_active(entry):
    if not entry.get("valid", True):
        return False
    expires_at = entry.get("expiration_time")
    return expires_at is None or datetime.fromisoformat(expires_at) > _now()


def _select(entries, server_type, ckey, active_only):
    return [
        e
        for e in entries
        if (server_type is None or e.get("server_type") == server_type)
        and (ckey is None or e["player_ckey"] == ckey)
        and (not active_only or is_active(e))
    ]


def _validity(duration, hours_per_unit):
    issued = _now()
    expires = None
    if duration:
        expires = _iso(issued + timedelta(hours=float(duration) * hours_per_unit))
    return {"issue_time": _iso(issued), "expiration_time": expires, "valid": True}


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat()
