import asyncio
import html
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

import discord

from .store import canonical_ckey

log = logging.getLogger("red.sscentral.tickets")

DESCRIPTION_MAX = 4096
LINE_MAX = 1000
KEPT_TICKETS = 500

TYPE_NAMES = {"Admin": "Админ-тикет", "Mentor": "Ментор-тикет", "Private Message": "ЛС от администратора"}
SYSTEM_SENDER = "TicketManager"

COLOR_ANSWERED = 0x41F097
COLOR_NO_ADMINS = 0xFF0000
COLOR_URGENT = 0x800000
COLOR_CLOSED = 0x95A5A6


@dataclass
class TicketLog:
    server: str
    round_id: int
    ticket_id: int
    player: str
    ticket_type: str = "Admin"
    character: Optional[str] = None
    admins_online: int = 0
    urgent: bool = False
    closed: bool = False
    lines: list = field(default_factory=list)
    message: Optional[discord.Message] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TicketRelay:
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self.tickets = OrderedDict()
        self.last_by_player = {}

    async def handle(self, event):
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return
        ticket = self._ticket_for(event)
        async with ticket.lock:
            _update_state(ticket, event)
            line = _line(event, ticket.player)
            if line:
                await self._post(channel, ticket, line)

    def _ticket_for(self, event):
        key = (event["server"], event["round_id"], event["ticket_id"])
        ticket = self.tickets.get(key)
        if not ticket:
            ticket = TicketLog(event["server"], event["round_id"], event["ticket_id"], canonical_ckey(event["player_ckey"]))
            self.tickets[key] = ticket
            if len(self.tickets) > KEPT_TICKETS:
                self.tickets.popitem(last=False)
        return ticket

    async def _post(self, channel, ticket, line):
        if ticket.message and len("\n".join([*ticket.lines, line])) > DESCRIPTION_MAX:
            ticket.lines = [f"**[Начало тикета]({ticket.message.jump_url})**"]
            ticket.message = None
        elif not ticket.message and not ticket.lines and ticket.player in self.last_by_player:
            ticket.lines.append(f"**[Прошлый тикет игрока]({self.last_by_player[ticket.player]})**")
        ticket.lines.append(line)

        try:
            if ticket.message:
                await ticket.message.edit(embed=_embed(ticket))
            else:
                ticket.message = await channel.send(embed=_embed(ticket))
                self.last_by_player[ticket.player] = ticket.message.jump_url
        except discord.NotFound:
            ticket.message = None
        except discord.HTTPException as error:
            log.warning("ticket relay failed: %s", error)


def _update_state(ticket, event):
    ticket.ticket_type = event.get("ticket_type") or ticket.ticket_type
    ticket.character = ticket.character or event.get("character")
    ticket.admins_online = event.get("admins_online", 0)
    ticket.urgent = ticket.urgent or bool(event.get("urgent"))
    action = event["action"]
    if action in ("Closed", "Resolved"):
        ticket.closed = True
    elif action in ("Opened", "Reopened"):
        ticket.closed = False


def _line(event, player):
    action = event["action"]
    sender = discord.utils.escape_markdown(event.get("sender") or "")
    text = discord.utils.escape_markdown(html.unescape(event.get("message") or ""))[:LINE_MAX]
    stamp = f"**{event['round_time']}** " if event.get("round_time") else ""

    if action == "Opened":
        # a PM ticket also reports the admin's first message as a Reply
        if event.get("ticket_type") == "Private Message":
            return None
        icon = ":inbox_tray:" if event.get("admins_online") else ":sos:"
        return f"{icon} {stamp}**{sender}:** {text}"
    if action == "Reply":
        icon = ":inbox_tray:" if canonical_ckey(event.get("sender") or "") == player else ":outbox_tray:"
        return f"{icon} {stamp}**{sender}:** {text}"
    if action == "Assigned":
        return f":hammer: {stamp}{text}"

    automatic = event.get("sender") == SYSTEM_SENDER
    notes = {
        "Closed": (":lock:", "закрыт автоматически" if automatic else f"{sender} закрыл тикет"),
        "Resolved": (":white_check_mark:", "решён автоматически" if automatic else f"{sender} решил тикет"),
        "Reopened": (":unlock:", f"{sender} снова открыл тикет"),
        "Convert": (":repeat:", f"{sender} перевёл тикет: {TYPE_NAMES.get(event.get('ticket_type'), event.get('ticket_type'))}"),
        "Unassigned": (":wave:", f"{sender} отказался от тикета"),
        "Disconnected": (":electric_plug:", f"{sender} отключился"),
        "Reconnected": (":electric_plug:", f"{sender} подключился"),
    }
    icon, note = notes.get(action, (":grey_question:", f"{action}: {text}"))
    return f"{icon} {stamp}_{note}_"


def _embed(ticket):
    name = TYPE_NAMES.get(ticket.ticket_type, ticket.ticket_type)
    embed = discord.Embed(title=f"{name} #{ticket.ticket_id}", description="\n".join(ticket.lines), color=_color(ticket))
    author = f"{ticket.player} ({ticket.character})" if ticket.character else ticket.player
    embed.set_author(name=author[:256])
    embed.set_footer(text=f"{ticket.server} (раунд {ticket.round_id})"[:2048])
    return embed


def _color(ticket):
    if ticket.closed:
        return COLOR_CLOSED
    if ticket.urgent:
        return COLOR_URGENT
    return COLOR_ANSWERED if ticket.admins_online else COLOR_NO_ADMINS
