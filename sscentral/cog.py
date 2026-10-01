import os
from dataclasses import dataclass
from typing import Optional, Union

import logging

import aiohttp
import discord
from aiohttp import web
from redbot.core import commands

from .api import Api
from .store import LinkConflict, Store, canonical_ckey

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
        )


class SSCentral(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = Settings.from_env()
        self.store = Store(self)
        self.session = None
        self.runner = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        api = Api(self.bot, self.store, self.settings, self.session, self.announce_ban)
        self.runner = web.AppRunner(api.build_app())
        await self.runner.setup()
        await web.TCPSite(self.runner, "0.0.0.0", self.settings.port).start()

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

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
    permanent = ban.get("duration_hours") is None
    job = ban.get("job")
    if job:
        kind, color = "Джоббан", 0x3498DB
    elif permanent:
        kind, color = "Перманентный", 0xE74C3C
    else:
        kind, color = "Временный", 0xE67E22
    embed = discord.Embed(title=f"Бан: {ban['player_ckey']}", color=color)
    embed.add_field(name="Тип", value=kind)
    embed.add_field(name="Срок", value="навсегда" if permanent else _hours_text(ban["duration_hours"]))
    if job:
        embed.add_field(name="Роли", value=job.replace(",", ", "), inline=False)
    embed.add_field(name="Причина", value=(ban.get("reason") or "не указана")[:1000], inline=False)
    if show_admin:
        embed.add_field(name="Админ", value=ban["admin_ckey"])
    if ban.get("round_id"):
        embed.add_field(name="Раунд", value=str(ban["round_id"]))
    return embed


def _hours_text(hours):
    hours = float(hours)
    if hours < 24:
        return f"{hours:g} ч."
    days = hours / 24
    return f"{days:g} дн."


def _ban_line(entry):
    expires = entry.get("expiration_time") or "навсегда"
    return f"{entry['issue_time'][:10]} {entry.get('bantype', '')} до {expires[:10]}: {entry.get('reason', '')[:80]}"
