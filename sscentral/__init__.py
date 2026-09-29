from .cog import SSCentral


async def setup(bot):
    await bot.add_cog(SSCentral(bot))
