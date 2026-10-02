from .cog import StatusCard


async def setup(bot):
    await bot.add_cog(StatusCard(bot))
