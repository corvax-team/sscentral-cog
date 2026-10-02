import asyncio
import logging
import os
from dataclasses import dataclass

import discord
from discord.ext import tasks
from redbot.core import Config, commands

from .byond import topic

log = logging.getLogger("red.ss13status")

CONFIG_ID = 0x55135747
UPDATE_SECONDS = 60


@dataclass
class Settings:
    host: str
    port: int
    channel_id: int
    name: str
    addresses: list

    @classmethod
    def from_env(cls):
        raw = os.environ.get("SS13STATUS_ADDRESSES", "")
        return cls(
            host=os.environ.get("SS13STATUS_HOST", "127.0.0.1"),
            port=int(os.environ.get("SS13STATUS_PORT", "1337")),
            channel_id=int(os.environ.get("SS13STATUS_CHANNEL", "0")),
            name=os.environ.get("SS13STATUS_NAME", "SS13"),
            addresses=[a.strip() for a in raw.split(";") if a.strip()],
        )


class StatusCard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = Settings.from_env()
        self.config = Config.get_conf(self, identifier=CONFIG_ID, force_registration=True)
        self.config.register_global(message_id=0)
        self.message = None

    async def cog_load(self):
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()

    @commands.command()
    async def status(self, ctx):
        """Состояние сервера"""
        embed, _ = await self._build_embed()
        await ctx.send(embed=embed)

    @tasks.loop(seconds=UPDATE_SECONDS)
    async def refresh(self):
        embed, players = await self._build_embed()
        await self._update_card(embed)
        await self._update_presence(players)

    @refresh.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_red_ready()

    async def _build_embed(self):
        try:
            data = await topic(self.settings.host, self.settings.port, "?status")
        except (OSError, asyncio.TimeoutError):
            data = None
        if not data:
            embed = discord.Embed(title=self.settings.name, color=0x95A5A6)
            embed.add_field(name="Состояние", value="Сервер недоступен или перезапускается", inline=False)
            return self._with_addresses(embed), None

        players = int(data.get("players", 0))
        embed = discord.Embed(title=self.settings.name, color=0x2ECC71)
        embed.add_field(name="Онлайн", value=_plural(players, "игрок", "игрока", "игроков"))
        embed.add_field(name="Карта", value=data.get("map_name", "?"))
        embed.add_field(name="Раунд", value=_round_text(data))
        embed.add_field(name="Уровень", value=_security(data.get("security_level")))
        embed.add_field(name="Шаттл", value=_shuttle(data.get("shuttle_mode"), data.get("shuttle_timer")))
        embed.add_field(name="Админы онлайн", value=data.get("admins", "0"))
        return self._with_addresses(embed), players

    def _with_addresses(self, embed):
        if self.settings.addresses:
            embed.add_field(name="Подключение", value="\n".join(self.settings.addresses), inline=False)
        embed.set_footer(text="Обновляется раз в минуту")
        return embed

    async def _update_card(self, embed):
        channel = self.bot.get_channel(self.settings.channel_id)
        if not channel:
            return
        if self.message is None:
            self.message = await self._find_card(channel)
        try:
            if self.message:
                await self.message.edit(embed=embed)
            else:
                self.message = await channel.send(embed=embed)
                await self.config.message_id.set(self.message.id)
        except discord.NotFound:
            self.message = None
        except discord.HTTPException as error:
            log.warning("status card update failed: %s", error)

    async def _find_card(self, channel):
        message_id = await self.config.message_id()
        if not message_id:
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.HTTPException:
            return None

    async def _update_presence(self, players):
        text = f"{_plural(players, 'игрок', 'игрока', 'игроков')} на {self.settings.name}" if players is not None else "сервер недоступен"
        try:
            await self.bot.change_presence(activity=discord.Game(name=text))
        except discord.HTTPException:
            pass


def _round_text(data):
    round_id = data.get("round_id")
    seconds = int(float(data.get("round_duration", 0)))
    state = data.get("gamestate")
    if state in ("0", "1"):
        return "лобби"
    if state == "2":
        return "запускается"
    if state == "4":
        return "завершён"
    hours, minutes = divmod(seconds // 60, 60)
    duration = f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"
    return f"#{round_id}, идёт {duration}" if round_id else f"идёт {duration}"


def _security(level):
    names = {"green": "зелёный", "blue": "синий", "red": "красный", "delta": "дельта"}
    return names.get((level or "").lower(), level or "?")


def _shuttle(mode, timer):
    if not mode or mode in ("idle", "recalled"):
        return "не вызван"
    seconds = int(float(timer or 0))
    clock = f"{seconds // 60}:{seconds % 60:02d}"
    if mode in ("igniting", "escape", "endgame: game over"):
        return f"улетел ({clock})" if mode != "endgame: game over" else "раунд окончен"
    if mode == "docked":
        return f"на станции, отлёт через {clock}"
    if mode == "stranded":
        return "не может прибыть"
    return f"вызван, прибытие через {clock}"


def _plural(n, one, few, many):
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"
