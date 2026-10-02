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
| `SSCENTRAL_TICKETS_CHANNEL` | channel that gets one embed per ahelp ticket, edited as messages and admin actions come in, off when unset |

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

## ss13status

A second cog in this repo: keeps one message in a channel updated with the round state (players, map, round, alert level, shuttle, admins) and mirrors the player count in the bot presence. `[p]status` shows the same card on demand.

| Variable | Meaning |
|---|---|
| `SS13STATUS_HOST`, `SS13STATUS_PORT` | where the game's status port is reached, default `127.0.0.1:1337` |
| `SS13STATUS_CHANNEL` | channel for the card |
| `SS13STATUS_NAME` | server name shown as the card title |
| `SS13STATUS_ADDRESSES` | connection lines shown under the card, separated by `;` |
