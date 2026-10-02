import os

import aiomysql


class GameDb:
    def __init__(self):
        self.pool = None
        self.host = os.environ.get("SSCENTRAL_DB_HOST")
        self.port = int(os.environ.get("SSCENTRAL_DB_PORT", "3306"))
        self.name = os.environ.get("SSCENTRAL_DB_NAME", "")
        self.user = os.environ.get("SSCENTRAL_DB_USER", "")
        self.password = os.environ.get("SSCENTRAL_DB_PASSWORD", "")

    @property
    def enabled(self):
        return bool(self.host and self.name and self.user)

    async def connect(self):
        if self.enabled:
            self.pool = await aiomysql.create_pool(
                host=self.host, port=self.port, db=self.name, user=self.user,
                password=self.password, charset="utf8mb4", autocommit=True, minsize=0, maxsize=3,
            )

    async def close(self):
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()

    async def notes(self, ckey, limit=15):
        return await self._rows(
            "SELECT id, adminckey, text, timestamp, severity, secret, round_id FROM messages "
            "WHERE targetckey = %s AND type = 'note' AND deleted = 0 "
            "AND (expire_timestamp IS NULL OR expire_timestamp > NOW()) "
            "ORDER BY timestamp DESC LIMIT %s",
            (ckey, limit),
        )

    async def player(self, ckey):
        rows = await self._rows(
            "SELECT ckey, byond_key, firstseen, lastseen, firstseen_round_id, lastseen_round_id, accountjoindate "
            "FROM player WHERE ckey = %s",
            (ckey,),
        )
        return rows[0] if rows else None

    async def playtime(self, ckey):
        rows = await self._rows("SELECT job, minutes FROM role_time WHERE ckey = %s ORDER BY minutes DESC", (ckey,))
        return rows

    async def counts(self, ckey):
        rows = await self._rows(
            "SELECT "
            "(SELECT COUNT(*) FROM messages WHERE targetckey = %s AND type = 'note' AND deleted = 0) AS notes, "
            "(SELECT COUNT(*) FROM ban WHERE ckey = %s AND unbanned_datetime IS NULL "
            "AND (expiration_time IS NULL OR expiration_time > NOW())) AS active_bans, "
            "(SELECT COUNT(*) FROM ban WHERE ckey = %s) AS total_bans",
            (ckey, ckey, ckey),
        )
        return rows[0]

    async def alts(self, ckey):
        return await self._rows(
            "SELECT o.ckey, "
            "SUM(o.ip = s.ip) > 0 AS same_ip, SUM(o.computerid = s.computerid) > 0 AS same_cid, "
            "MAX(o.datetime) AS last_seen "
            "FROM connection_log s JOIN connection_log o "
            "ON (o.ip = s.ip OR o.computerid = s.computerid) AND o.ckey <> s.ckey "
            "WHERE s.ckey = %s GROUP BY o.ckey ORDER BY last_seen DESC LIMIT 20",
            (ckey,),
        )

    async def _rows(self, sql, params):
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                return await cur.fetchall()
