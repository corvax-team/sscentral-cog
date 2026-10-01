# sscentral-cog

A [Red-DiscordBot](https://github.com/Cog-Creators/Red-DiscordBot) cog that acts as SS Central for an SS13 server built on BandaStation: it serves the HTTP API the game calls and lets staff manage the same data from Discord.

## Settings

Read from the environment when the cog loads.

| Variable | Meaning |
|---|---|
| `SSCENTRAL_API_TOKEN` | bearer token, same value as `SS_CENTRAL_TOKEN` in the game config |
| `SSCENTRAL_CLIENT_ID` | Discord application ID, must be the application of the bot running the cog |
| `SSCENTRAL_CLIENT_SECRET` | OAuth2 client secret of that application |
| `SSCENTRAL_PUBLIC_URL` | base URL players reach, same value as `SS_CENTRAL_URL` in the game config |
| `SSCENTRAL_GUILD_ID` | Discord server that players are added to after linking |
| `SSCENTRAL_SERVER_TYPE` | same value as `SERVER_TYPE` in the game config |
| `SSCENTRAL_PORT` | listen port, default `8440` |
| `SSCENTRAL_BANS_CHANNEL` | channel that gets an embed for every ban the game reports, off when unset |
| `SSCENTRAL_BANS_SHOW_ADMIN` | `true` to include the admin's ckey in that embed |

Add `<SSCENTRAL_PUBLIC_URL>/oauth/callback` as a redirect in the Discord application. The bot needs the Create Invite permission to add players to the server.

Only `/oauth/login` and `/oauth/callback` should be reachable from the internet. The read endpoints carry no authentication because the game sends none, so keep the rest on a private address.

## Commands

All under `[p]central`, for Red admins. Tier changes are owner only.

| Command | Does |
|---|---|
| `link <member> <ckey>` | link by hand |
| `unlink <ckey>` | remove a link |
| `whois <member or ckey>` | show link, whitelist, tier and ban count |
| `whitelist add <ckey> [days]` | add to the whitelist |
| `whitelist remove <ckey> [days] <reason>` | remove and block from the whitelist |
| `whitelist list` | active whitelist |
| `tier set <ckey> <tier> [days]` | grant a donate tier |
| `tier clear <ckey>` | remove the tier |
| `tier list` | active tiers |
| `bans <ckey>` | bans reported by the game |
| `reannounce <id or all>` | post stored bans to the bans channel again |
