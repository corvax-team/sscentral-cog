import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import aiohttp
import discord
from aiohttp import web
from redbot.core import commands

from .api import Api
from .gamedb import GameDb
from .store import LinkConflict, Store, canonical_ckey
from .tickets import TicketRelay

log = logging.getLogger("red.sscentral")


@dataclass
class Settings:
    api_token: str
    client_id: str
    client_secret: str
    public_url: str
    guild_id: int
    server_type: str
    port: int
    bans_channel_id: int
    bans_show_admin: bool
    tickets_channel_id: int

    @property
    def redirect_uri(self):
        return f"{self.public_url}/oauth/callback"

    @classmethod
    def from_env(cls):
        return cls(
            api_token=_required("SSCENTRAL_API_TOKEN"),
            client_id=_required("SSCENTRAL_CLIENT_ID"),
            client_secret=_required("SSCENTRAL_CLIENT_SECRET"),
            public_url=_required("SSCENTRAL_PUBLIC_URL").rstrip("/"),
            guild_id=int(_required("SSCENTRAL_GUILD_ID")),
            server_type=os.environ.get("SSCENTRAL_SERVER_TYPE", "default"),
            port=int(os.environ.get("SSCENTRAL_PORT", "8440")),
            bans_channel_id=int(os.environ.get("SSCENTRAL_BANS_CHANNEL", "0")),
            bans_show_admin=os.environ.get("SSCENTRAL_BANS_SHOW_ADMIN", "false").lower() == "true",
            tickets_channel_id=int(os.environ.get("SSCENTRAL_TICKETS_CHANNEL", "0")),
        )


class SSCentral(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = Settings.from_env()
        self.store = Store(self)
        self.tickets = TicketRelay(bot, self.settings.tickets_channel_id)
        self.gamedb = GameDb()
        self.session = None
        self.runner = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        api = Api(self.bot, self.store, self.settings, self.session, self.announce_ban, self.tickets.handle)
        self.runner = web.AppRunner(api.build_app())
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", self.settings.port).start()
        await self.gamedb.connect()

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()
        await self.gamedb.close()

    @commands.group()
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def central(self, ctx):
        """Управление SS Central"""

    @central.command()
    async def link(self, ctx, member: discord.Member, ckey: str):
        """Привязать Discord к ckey"""
        ckey = canonical_ckey(ckey)
        try:
            await self.store.link(ckey, member.id)
        except LinkConflict as conflict:
            taken = "Этот ckey" if str(conflict) == "ckey" else "Этот Discord"
            await ctx.send(f"{taken} уже привязан, сначала сделайте unlink")
            return
        await ctx.send(f"{member.mention} привязан к `{ckey}`")

    @central.command()
    async def unlink(self, ctx, ckey: str):
        """Отвязать Discord от ckey"""
        ckey = canonical_ckey(ckey)
        removed = await self.store.unlink(ckey)
        await ctx.send(f"`{ckey}` отвязан" if removed else f"`{ckey}` не был привязан")

    @central.command()
    async def whois(self, ctx, target: Union[discord.Member, str]):
        """Показать привязку, вайтлист и тир игрока"""
        player = await self._find_player(target)
        if not player:
            await ctx.send("Привязка не найдена")
            return
        await ctx.send(await self._describe(player), allowed_mentions=discord.AllowedMentions.none())

    @central.group()
    async def whitelist(self, ctx):
        """Вайтлист"""

    @whitelist.command(name="add")
    async def whitelist_add(self, ctx, ckey: str, days: Optional[int] = 0):
        """Добавить в вайтлист, 0 дней значит бессрочно"""
        ckey = canonical_ckey(ckey)
        admin = await self._ckey_of(ctx.author)
        await self.store.add_whitelist(ckey, admin, self.settings.server_type, days)
        await ctx.send(f"`{ckey}` добавлен в вайтлист {_term(days)}")

    @whitelist.command(name="remove")
    async def whitelist_remove(self, ctx, ckey: str, days: Optional[int] = 0, *, reason: str):
        """Выписать из вайтлиста, 0 дней значит бессрочно"""
        ckey = canonical_ckey(ckey)
        admin = await self._ckey_of(ctx.author)
        await self.store.add_whitelist_ban(ckey, admin, self.settings.server_type, days, reason)
        await ctx.send(f"`{ckey}` выписан из вайтлиста {_term(days)}")

    @whitelist.command(name="list")
    async def whitelist_list(self, ctx):
        """Показать активный вайтлист"""
        entries = await self.store.whitelist(self.settings.server_type)
        ckeys = sorted({e["player_ckey"] for e in entries})
        await ctx.send(_listing("Вайтлист", ckeys))

    @central.group()
    async def tier(self, ctx):
        """Тиры подписки"""

    @tier.command(name="set")
    @commands.is_owner()
    async def tier_set(self, ctx, ckey: str, tier: int, days: Optional[int] = 0):
        """Выдать тир, 0 дней значит бессрочно"""
        ckey = canonical_ckey(ckey)
        await self.store.set_tier(ckey, tier, days)
        await ctx.send(f"`{ckey}` получил тир {tier} {_term(days)}")

    @tier.command(name="clear")
    @commands.is_owner()
    async def tier_clear(self, ctx, ckey: str):
        """Снять тир"""
        ckey = canonical_ckey(ckey)
        cleared = await self.store.clear_tier(ckey)
        await ctx.send(f"Тир `{ckey}` снят" if cleared else f"У `{ckey}` нет активного тира")

    @tier.command(name="list")
    async def tier_list(self, ctx):
        """Показать активные тиры"""
        entries = await self.store.donates()
        lines = [f"{e['player_ckey']}: {e['tier']}" for e in entries]
        await ctx.send(_listing("Тиры", sorted(lines)))

    @central.command()
    async def bans(self, ctx, ckey: str):
        """Показать баны игрока, полученные от сервера"""
        ckey = canonical_ckey(ckey)
        entries = await self.store.bans(ckey)
        lines = [_ban_line(e) for e in entries[-15:]]
        await ctx.send(_listing(f"Баны {ckey}", lines))

    @central.command()
    async def notes(self, ctx, ckey: str):
        """Заметки администрации об игроке"""
        if not await self._gamedb_ready(ctx):
            return
        ckey = canonical_ckey(ckey)
        notes = await self.gamedb.notes(ckey)
        if not notes:
            await ctx.send(f"У `{ckey}` нет заметок")
            return
        embed = discord.Embed(title=f"Заметки: {ckey}", color=0x9B59B6)
        for note in notes:
            when = note["timestamp"].strftime("%d.%m.%Y %H:%M")
            flags = f" [{_severity(note['severity'])}]" if note["severity"] and note["severity"] != "none" else ""
            secret = " (скрытая)" if note["secret"] else ""
            embed.add_field(name=f"{when} {note['adminckey']}{flags}{secret}", value=note["text"][:1000], inline=False)
        embed.set_footer(text=f"Последние {len(notes)}")
        await ctx.send(embed=embed)

    @central.command()
    async def player(self, ctx, ckey: str):
        """Сводка по игроку из базы сервера"""
        if not await self._gamedb_ready(ctx):
            return
        ckey = canonical_ckey(ckey)
        info = await self.gamedb.player(ckey)
        if not info:
            await ctx.send(f"`{ckey}` ни разу не заходил на сервер")
            return
        counts = await self.gamedb.counts(ckey)
        playtime = await self.gamedb.playtime(ckey)
        living = sum(r["minutes"] for r in playtime if r["job"] == "Living")
        top = ", ".join(f"{r['job']} {r['minutes'] // 60} ч" for r in playtime if r["job"] not in ("Living", "Ghost"))[:300]
        link = await self.store.player_by_ckey(ckey)
        embed = discord.Embed(title=f"Игрок: {ckey}", color=0x3498DB)
        embed.add_field(name="BYOND", value=info["byond_key"] or ckey)
        embed.add_field(name="Discord", value=f"<@{link['discord_id']}>" if link else "не привязан")
        embed.add_field(name="Аккаунт создан", value=str(info["accountjoindate"] or "?"))
        embed.add_field(name="Первый вход", value=f"{info['firstseen']:%d.%m.%Y} (раунд {info['firstseen_round_id']})")
        embed.add_field(name="Последний вход", value=f"{info['lastseen']:%d.%m.%Y} (раунд {info['lastseen_round_id']})")
        embed.add_field(name="Наиграно", value=f"{living // 60} ч")
        embed.add_field(name="Заметки", value=str(counts["notes"]))
        embed.add_field(name="Баны", value=f"{counts['active_bans']} активных, {counts['total_bans']} всего")
        if top:
            embed.add_field(name="Роли", value=top, inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @central.command()
    async def alts(self, ctx, ckey: str):
        """Другие ckey с тех же подключений"""
        if not await self._gamedb_ready(ctx):
            return
        ckey = canonical_ckey(ckey)
        rows = await self.gamedb.alts(ckey)
        if not rows:
            await ctx.send(f"Совпадений по подключениям для `{ckey}` нет")
            return
        lines = []
        for row in rows:
            how = " и ".join(x for x, ok in (("IP", row["same_ip"]), ("CID", row["same_cid"])) if ok)
            lines.append(f"{row['ckey']}: {how}, последний раз {row['last_seen']:%d.%m.%Y}")
        await ctx.send(_listing(f"Возможные альты {ckey}", lines))

    async def _gamedb_ready(self, ctx):
        if self.gamedb.pool:
            return True
        await ctx.send("База сервера не подключена")
        return False

    @central.command()
    async def reannounce(self, ctx, ban_id: str):
        """Повторно отправить бан в канал банов, id бана или all"""
        entries = await self.store.bans()
        if ban_id != "all":
            entries = [e for e in entries if str(e["id"]) == ban_id]
        if not entries:
            await ctx.send("Бан не найден")
            return
        for entry in entries:
            await self.announce_ban(entry)
        await ctx.send(f"Отправлено: {len(entries)}")

    async def announce_ban(self, ban):
        channel = self.bot.get_channel(self.settings.bans_channel_id)
        if not channel:
            return
        try:
            await channel.send(embed=_ban_embed(ban, self.settings.bans_show_admin))
        except discord.HTTPException as error:
            log.warning("ban announce failed: %s", error)

    async def _find_player(self, target):
        if isinstance(target, discord.Member):
            return await self.store.player_by_discord(target.id)
        return await self.store.player_by_ckey(canonical_ckey(target))

    async def _describe(self, player):
        ckey = player["ckey"]
        whitelisted = await self.store.whitelist(self.settings.server_type, ckey)
        tiers = [e["tier"] for e in await self.store.donates(ckey)]
        bans = await self.store.bans(ckey)
        return (
            f"ckey: `{ckey}`\n"
            f"Discord: <@{player['discord_id']}>\n"
            f"Вайтлист: {'да' if whitelisted else 'нет'}\n"
            f"Тир: {max(tiers) if tiers else 'нет'}\n"
            f"Банов: {len(bans)}"
        )

    async def _ckey_of(self, member):
        player = await self.store.player_by_discord(member.id)
        return player["ckey"] if player else canonical_ckey(member.name)


def _required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _term(days):
    return f"на {days} дн." if days else "бессрочно"


def _listing(title, lines):
    if not lines:
        return f"{title}: пусто"
    body = "\n".join(lines)
    return f"{title} ({len(lines)}):\n```\n{body[:1800]}\n```"


def _ban_embed(ban, show_admin):
    hours = ban.get("duration_hours")
    permanent = hours is None
    job = ban.get("job")
    if job:
        kind = "Джоббан"
    elif permanent:
        kind = "Перманентный бан"
    else:
        kind = "Временный бан"
    issued = _moscow(ban["issue_time"])

    lines = [f"**Нарушитель**: `{ban['player_ckey']}`"]
    if show_admin:
        lines.append(f"**Администратор**: `{ban['admin_ckey']}`")
    if permanent:
        lines += ["", f"**Выдан**: {issued}"]
    else:
        lines += ["", f"**Выдан**: {issued} по {_moscow(ban['expiration_time'])}", f"**Длительность**: {_hours_text(hours)}"]
    if job:
        lines.append(f"**Роли**: {job.replace(',', ', ')}")
    lines += ["", f"**Причина**: {(ban.get('reason') or 'не указана')[:1500]}"]
    if ban.get("round_id"):
        lines.append(f"**Раунд**: {ban['round_id']}")

    return discord.Embed(title=f"{kind} #{ban['id']}", description="\n".join(lines), color=_ban_color(job, hours))


def _ban_color(job, hours):
    if job:
        return 0x3498DB
    if hours is None:
        return 0x992D22
    if float(hours) >= 24 * 30:
        return 0xE74C3C
    if float(hours) >= 24 * 7:
        return 0xE67E22
    return 0xF1C40F


def _moscow(iso):
    moment = datetime.fromisoformat(iso).astimezone(timezone(timedelta(hours=3)))
    return moment.strftime("%d.%m.%Y %H:%M")


def _hours_text(hours):
    hours = float(hours)
    if hours < 24:
        return _plural(round(hours), "час", "часа", "часов")
    return _plural(round(hours / 24), "день", "дня", "дней")


def _severity(value):
    return {"high": "серьёзная", "medium": "средняя", "minor": "мелкая"}.get(value, value)


def _plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def _ban_line(entry):
    expires = entry.get("expiration_time") or "навсегда"
    return f"{entry['issue_time'][:10]} {entry.get('bantype', '')} до {expires[:10]}: {entry.get('reason', '')[:80]}"
