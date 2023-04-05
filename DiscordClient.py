import discord
import logging
import asyncio
from RingDict import RingDict

class DiscordClient(discord.Client):
    targetChannel : discord.channel = None
    botNick = None
    
    lastHeard = RingDict(size=10)
    #    "AD8IS-10": {
    #        "thread":1234567890,   #the discord thread id used to converse with this client
    #        }
    #    }

    def __init__(self, botNick, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.botNick = botNick

    async def boot(self,botSecret):
        logging.info("Discord logging in...", extra={'className': self.__class__.__name__})
        await self.login(token=botSecret)
        logging.info("Discord logged in. Connecting...", extra={'className': self.__class__.__name__})
        discord_task = asyncio.create_task(self.connect())
        logging.info("Connection running in background. Waiting for ready.", extra={'className': self.__class__.__name__})
        await self.wait_until_ready()

    async def on_ready(self):
        logging.info(f'Logged on as {self.user}!', extra={'className': self.__class__.__name__})

