import hmac
import logging
import secrets
import time
from html import escape
from urllib.parse import urlencode

from aiohttp import web

from .store import LinkConflict, canonical_ckey

log = logging.getLogger("red.sscentral.api")

DISCORD_API = "https://discord.com/api/v10"
DISCORD_AUTHORIZE = "https://discord.com/oauth2/authorize"
LOGIN_TOKEN_TTL = 600


class Api:
    def __init__(self, bot, store, settings, session):
        self.bot = bot
        self.store = store
        self.settings = settings
        self.session = session
        self.login_tokens = {}

    def build_app(self):
        app = web.Application(middlewares=[self._require_bearer])
        app.add_routes(
            [
                web.get("/", self.health),
                web.get("/players/ckey/{ckey}", self.player_by_ckey),
                web.get("/players/discord/{discord_id}", self.player_by_discord),
                web.get("/whitelists", self.whitelists),
                web.get("/whitelists/ckeys", self.whitelist_ckeys),
                web.post("/whitelists", self.add_whitelist),
                web.post("/whitelist_bans", self.add_whitelist_ban),
                web.get("/donates", self.donates),
                web.post("/bans", self.add_ban),
                web.post("/oauth/token", self.oauth_token),
                web.get("/oauth/login", self.oauth_login),
                web.get("/oauth/callback", self.oauth_callback),
            ]
        )
        return app

    async def health(self, request):
        return web.json_response({"status": "ok"})

    async def player_by_ckey(self, request):
        ckey = canonical_ckey(request.match_info["ckey"])
        return _player_response(await self.store.player_by_ckey(ckey))

    async def player_by_discord(self, request):
        discord_id = request.match_info["discord_id"]
        return _player_response(await self.store.player_by_discord(discord_id))

    async def whitelists(self, request):
        entries = await self.store.whitelist(
            request.query.get("server_type"), _query_ckey(request), _active_only(request)
        )
        return _page(request, entries)

    async def whitelist_ckeys(self, request):
        entries = await self.store.whitelist(
            request.query.get("server_type"), None, _active_only(request)
        )
        return _page(request, sorted({e["player_ckey"] for e in entries}))

    async def add_whitelist(self, request):
        body = await request.json()
        ckey = canonical_ckey(body["player_ckey"])
        server_type = body["server_type"]
        if not await self.store.player_by_ckey(ckey):
            return _error(404, "Player not found")
        if await self.store.whitelist_bans(server_type, ckey):
            return _error(409, "Player is banned from this whitelist")
        entry = await self.store.add_whitelist(
            ckey, canonical_ckey(body["admin_ckey"]), server_type, body.get("duration_days")
        )
        return web.json_response(entry, status=201)

    async def add_whitelist_ban(self, request):
        body = await request.json()
        entry = await self.store.add_whitelist_ban(
            canonical_ckey(body["player_ckey"]),
            canonical_ckey(body["admin_ckey"]),
            body["server_type"],
            body.get("duration_days"),
            body.get("reason", ""),
        )
        return web.json_response(entry, status=201)

    async def donates(self, request):
        entries = await self.store.donates(_query_ckey(request), _active_only(request))
        return _page(request, entries)

    async def add_ban(self, request):
        body = await request.json()
        body["player_ckey"] = canonical_ckey(body["player_ckey"])
        body["admin_ckey"] = canonical_ckey(body["admin_ckey"])
        entry = await self.store.add_ban(body)
        return web.json_response(entry, status=201)

    async def oauth_token(self, request):
        ckey = canonical_ckey(request.query.get("ckey", ""))
        if not ckey:
            return _error(400, "ckey is required")
        self._drop_expired_tokens()
        token = secrets.token_urlsafe(24)
        self.login_tokens[token] = (ckey, time.monotonic() + LOGIN_TOKEN_TTL)
        return web.json_response(token, status=201)

    async def oauth_login(self, request):
        token = request.query.get("token", "")
        if not self._ckey_for(token):
            return _html(400, "Ссылка устарела", "Запросите привязку в игре ещё раз.")
        params = {
            "client_id": self.settings.client_id,
            "response_type": "code",
            "redirect_uri": self.settings.redirect_uri,
            "scope": "identify guilds.join",
            "state": token,
        }
        raise web.HTTPFound(f"{DISCORD_AUTHORIZE}?{urlencode(params)}")

    async def oauth_callback(self, request):
        ckey = self._ckey_for(request.query.get("state", ""))
        code = request.query.get("code")
        if not ckey or not code:
            return _html(400, "Привязка не удалась", "Запросите привязку в игре ещё раз.")

        access_token = await self._exchange_code(code)
        user = access_token and await self._fetch_user(access_token)
        if not user:
            return _html(502, "Discord не ответил", "Попробуйте ещё раз через минуту.")

        try:
            await self.store.link(ckey, user["id"])
        except LinkConflict as conflict:
            return _html(409, "Уже привязано", _conflict_text(conflict, ckey))

        self.login_tokens.pop(request.query["state"], None)
        await self._join_guild(user["id"], access_token)
        name = escape(user.get("global_name") or user["username"])
        return _html(200, "Готово", f"Discord {name} привязан к {escape(ckey)}. Окно можно закрыть.")

    @web.middleware
    async def _require_bearer(self, request, handler):
        if request.method == "POST":
            sent = request.headers.get("Authorization", "")
            expected = f"Bearer {self.settings.api_token}"
            if not hmac.compare_digest(sent.encode(), expected.encode()):
                return _error(401, "Invalid token")
        return await handler(request)

    def _ckey_for(self, token):
        ckey, expires_at = self.login_tokens.get(token, (None, 0))
        return ckey if expires_at > time.monotonic() else None

    def _drop_expired_tokens(self):
        now = time.monotonic()
        self.login_tokens = {t: v for t, v in self.login_tokens.items() if v[1] > now}

    async def _exchange_code(self, code):
        form = {
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
        }
        async with self.session.post(f"{DISCORD_API}/oauth2/token", data=form) as response:
            if response.status != 200:
                log.warning("code exchange failed with %s", response.status)
                return None
            return (await response.json())["access_token"]

    async def _fetch_user(self, access_token):
        headers = {"Authorization": f"Bearer {access_token}"}
        async with self.session.get(f"{DISCORD_API}/users/@me", headers=headers) as response:
            return await response.json() if response.status == 200 else None

    async def _join_guild(self, user_id, access_token):
        url = f"{DISCORD_API}/guilds/{self.settings.guild_id}/members/{user_id}"
        headers = {"Authorization": f"Bot {self.bot.http.token}"}
        payload = {"access_token": access_token}
        async with self.session.put(url, json=payload, headers=headers) as response:
            if response.status not in (201, 204):
                log.warning("guild join for %s failed with %s", user_id, response.status)


def _player_response(player):
    if not player:
        return _error(404, "Player not found")
    return web.json_response(player)


def _page(request, items):
    page = max(int(request.query.get("page", 1)), 1)
    size = max(int(request.query.get("page_size", 50)), 1)
    start = (page - 1) * size
    body = {"items": items[start : start + size], "total": len(items), "page": page, "page_size": size}
    return web.json_response(body)


def _query_ckey(request):
    raw = request.query.get("ckey")
    return canonical_ckey(raw) if raw else None


def _active_only(request):
    return request.query.get("active_only", "true").lower() != "false"


def _conflict_text(conflict, ckey):
    if str(conflict) == "ckey":
        return f"К {escape(ckey)} уже привязан другой Discord. Обратитесь к администрации."
    return "Этот Discord уже привязан к другому ckey. Обратитесь к администрации."


def _error(status, detail):
    return web.json_response({"detail": detail}, status=status)


def _html(status, title, text):
    page = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{background:#111;color:#ddd;font-family:sans-serif;"
        "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}"
        "div{text-align:center}h1{font-size:1.6em}</style></head>"
        f"<body><div><h1>{title}</h1><p>{text}</p></div></body></html>"
    )
    return web.Response(status=status, text=page, content_type="text/html")
